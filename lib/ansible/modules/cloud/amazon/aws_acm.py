#!/usr/bin/python
# Copyright (c) 2017 Ansible Project
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

ANSIBLE_METADATA = {'metadata_version': '0.1',
                    'status': ['preview'],
                    'supported_by': 'community'}

DOCUMENTATION = '''
module: aws_acm
short_description: Upload or delete certificates in the AWS Certificate Manager service
description:
  - Import or delete ACM certificates
  - This module does not currently interact with AWS-provided certificates.
  - The ACM API allows users to upload multiple certificates for the same domain name, 
    and even multiple identical certificates.
    This module attempts to restrict such freedoms, to be idempotent, as per the Ansible philosophy.
    It does this through applying AWS resource "Name" tags to ACM certificates.
  - When C(state=present), if there are multiple certificates in ACM with the corresponding tag, this task will fail.
  - When C(state=present), if there is one certificates in ACM with the corresponding tag and an identical body and chain, this task will succeed without effect.
  - When C(state=present), if there is one certificates in ACM with the corresponding tag and a different identical body, this task overwrite that certificate.
  - When C(state=absent) and C(certificate_arn) is defined, this module will delete the ACM resource with that ARN if it exists, and succeed without effect if it doesn't exist.
  - When C(state=absent) and C(domain_name) is defined, this module will delete all ACM resources in this AWS region with a corresponding domain name. If there are none, it will succeed without effect.
  - When C(state=absent) and C(certificate_arn) is not defined, and C(domain_name) is not defined, this module will delete all ACM resources in this AWS region with a corresponding tag. If there are none, it will succeed without effect.
  - Note that this will not work properly with keys of size 4096 bits, due to a limitation of the ACM API.
version_added: "2.10"
options:
  certificate:
    description:
      - The body of the PEM encoded public certificate. 
      - Required when C(state) is not C(absent).
      - If your certificate is in a file, use C(lookup('file', 'path/to/cert.pem')).
    type: str
    
  certificate_arn:
    description:
      - The ARN of a certificate in ACM to delete
      - Ignored when C(state) is C(present).
      - If C(state=absent), you must provide one of C(certificate_arn), C(domain_name) or C(name_tag).
      - If C(state=absent) and no resource exists with this ARN in this region, the task will succeed with no effect.
      - If C(state=absent) and the corresponding resource exists in a different region, this task may report success without deleting that resource.
    type: str
    
  certificate_chain:
    description:
      - The body of the PEM encoded chain for your certificate. 
      - If your certificate chain is in a file, use C(lookup('file', 'path/to/chain.pem')).
      - Ignored when C(state=absent)
    type: str

  domain_name:
    description:
      - The domain name of a certificate.
      - If C(state=present) this must not be specified. (The domain name is encoded within the public certificate's body.) 
      - If C(state=absent) and C(certificate_arn) is not provided, this task will delete all ACM certificates with this domain.
      - If C(state=absent) and C(certificate_arn) is provided, C(domain_name) must not be provided.
    type: str

  name_tag:
    description:
      - The unique identifier for tagging resources. 
      - This is to ensure Ansible can treat certificates idempotently, even though the ACM API allows duplicate certificates.
      - If C(state=preset), this must be specified.
      - If C(state=absent), you must provide one of C(certificate_arn), C(domain_name) or C(name_tag).

  private_key:
    description:
      - The body of the PEM encoded private key. 
      - Required when C(state) is C(present).
      - Ignored when C(state) is C(absent).
      - If your private key is in a file, use C(lookup('file', 'path/to/key.pem')).
    type: str
    
  state:
    description:
      - If C(state) is C(present) and no ACM resource
    choices: [present, absent]
    default: present
    type: str
requirements:
  - boto3
author:
  - Matthew Davis (Matthew.Davis.2@team.telstra.com)
extends_documentation_fragment:
    - aws
    - ec2
'''

EXAMPLES = '''

- name: upload a self-signed certificate
  aws_acm:
    certificate: "{{ lookup('file', 'cert.pem ) }}"
    privateKey: "{{ lookup('file', 'key.pem' ) }}"
    name_tag: my_cert # to be applied through an AWS tag as  "Name":"my_cert"
    region: ap-southeast-2 # AWS region

- name: create/update a certificate with a chain
  aws_acm:
    certificate: "{{ lookup('file', 'cert.pem ) }}"
    privateKey: "{{ lookup('file', 'key.pem' ) }}"
    name_tag: my_cert
    certificate_chain: "{{ lookup('file', 'chain.pem' ) }}"
    state: present
    region: ap-southeast-2
    
- name: delete the cert we just created
  aws_acm:
    name_tag: my_cert
    state: absent
    region: ap-southeast-2

- name: delete a certificate with a particular ARN
  aws_acm:
    certificate_arn:  arn:aws:acm:ap-southeast-2:123456789012:certificate/01234567-abcd-abcd-abcd-012345678901
    state: absent
    region: ap-southeast-2
  
- name: delete all certificates with a particular domain name
  aws_acm:
    domain_name: acm.ansible.com
    state: absent
    region: ap-southeast-2
  
'''

RETURN = '''
certificate:
  description: Information about the certificate which was uploaded
  type: complex
  returned: when state is present
  contains:
    arn:
      description: The ARN of the certificate in ACM
      type: str
      returned: when state is present
      sample: "arn:aws:acm:ap-southeast-2:123456789012:certificate/01234567-abcd-abcd-abcd-012345678901"
    domain_name:
      description: The domain name encoded within the public certificate
      type: str
      returned: when state is present
      sample: acm.ansible.com
arns:
  description: A list of the ARNs of the certificates in ACM which were deleted
  type: list
  returned: when state is absent
  sample: 
   - "arn:aws:acm:ap-southeast-2:123456789012:certificate/01234567-abcd-abcd-abcd-012345678901"
'''

from ansible.module_utils.aws.core import AnsibleAWSModule
from ansible.module_utils.ec2 import boto3_conn, ec2_argument_spec, get_aws_connection_info
from ansible.module_utils.aws.acm import ACMServiceManager

# For converting PEM bodies to something that can be easily compared
# e.g. ignore case, strip trailing whitespace
#
# There's probably a standard crypto library to do this, I'm being lazy
# But if I mistakenly think two identical certs with different format are the different,
# then the only impact is that changed=True when it should be changed=False
#
def standardize_pem(pem):
    if pem == None:
      return('')
    # eliminate whitespace
    for c in [' ','\n','\t']:
        pem = pem.replace(c,'')
        
    # I'm not sure whether the number of dashes at the top and bottom can vary
    # reduce all consecutive dashes to one dash
    # (The actual body never contains dashes)
    while '--' in pem:
      pem = pem.replace('--','-')
        
    return(pem.lower().strip())
   
# Returns True if two PEM encoded strings are the same
def pem_compare(a,b):
    return(standardize_pem(a) == standardize_pem(b))


def main():
    argument_spec = dict(
      certificate=dict(),
      certificate_arn=dict(alias=['arn']),
      certificate_chain=dict(),
      domain_name=dict(alias=['domain']),
      name_tag=dict(alias=['name']),
      private_key=dict(no_log=True),
      state=dict(default='present', choices=['present','absent'])
    )
    module = AnsibleAWSModule(argument_spec=argument_spec, supports_check_mode=True)
    acm = ACMServiceManager(module)
    
    # Check argument requirements
    if module.params['state'] == 'present':
      if not module.params['certificate']:
        module.fail_json(msg="Parameter 'certificate' must be specified if 'state' is specified as 'present'")
      elif module.params['certificate_arn']:
        module.fail_json(msg="Parameter 'certificate_arn' is only valid if parameter 'state' is specified as 'absent'")
      elif not module.params['name_tag']:
        module.fail_json(msg="Parameter 'name_tag' must be specified if parameter 'state' is specified as 'present'")
      elif not module.params['private_key']:
        module.fail_json(msg="Parameter 'private_key' must be specified if 'state' is specified as 'present'")
    else: # absent
    
      # exactly one of these should be specified
      absent_args = ['certificate_arn', 'domain_name', 'name_tag']
      if sum([(module.params[a] != None) for a in absent_args]) != 1:
        for a in absent_args:
          module.debug("%s is %s" % (a,module.params[a]))
        module.fail_json(msg="If 'state' is specified as 'absent' then exactly one of 'name_tag', certificate_arn' or 'domain_name' must be specified")

    if module.params['name_tag']:
      tags = {'Name':module.params['name_tag'] }
    else:
      tags = None

    region, ec2_url, aws_connect_kwargs = get_aws_connection_info(module, boto3=True)
    client = boto3_conn(module, conn_type='client', resource='acm',
                        region=region, endpoint=ec2_url, **aws_connect_kwargs)

    # fetch the list of certificates currently in ACM
    certificates = acm.get_certificates(client=client, 
                                        module=module, 
                                        domain_name=module.params['domain_name'], 
                                        arn=module.params['certificate_arn'],
                                        only_tags=tags)
    
    module.debug("Found %d corresponding certificates in ACM" % len(certificates))
    
    if module.params['state'] == 'present':
      if len(certificates) > 1:
        msg = "More than one certificate with Name=%s exists in ACM in this region" % module.params['name_tag']
        module.fail_json(msg=msg,certificates=certificates)
      elif len(certificates) == 1:
        # update the existing certificate
        module.debug("Existing certificate found in ACM")
        old_cert = certificates[0] # existing cert in ACM
        if ('tags' not in old_cert) or ('Name' not in old_cert['tags']) or (old_cert['tags']['Name'] != module.params['name_tag']):
          # shouldn't happen
          module.fail_json(msg="Internal error, unsure which certificate to update",certificate=old_cert)
          
        if 'certificate' not in old_cert:
          # shouldn't happen
          module.fail_json(msg="Internal error, unsure what the existing cert in ACM is",certificate=old_cert)
        
        # Are the existing certificate in ACM and the local certificate the same?  
        same = True
        same &= pem_compare(old_cert['certificate'],module.params['certificate'])
        if module.params['certificate_chain']:
          # Need to test this
          # not sure if Amazon appends the cert itself to the chain when self-signed
          same &= pem_compare(old_cert['certificate_chain'],module.params['certificate_chain'])
        else:
          # When there is no chain with a cert
          # it seems Amazon returns the cert itself as the chain
          same &= pem_compare(old_cert['certificate_chain'],module.params['certificate'])
        
        if same:
             module.debug("Existing certificate in ACM is the same, doing nothing")
             domain = acm.get_domain_of_cert(client=client,module=module,arn=old_cert['certificate_arn'])
             module.exit_json(certificate=dict(domain_name=domain, arn=old_cert['certificate_arn'] ), 
                              changed=False)
        else:
          module.debug("Existing certificate in ACM is different, overwriting")
          
          # update cert in ACM
          arn = acm.import_certificate(client,module,
                                       certificate=module.params['certificate'],
                                       private_key=module.params['private_key'],
                                       certificate_chain=module.params['certificate_chain'],
                                       arn=old_cert['certificate_arn'],
                                       tags=tags)
          domain = acm.get_domain_of_cert(client=client,module=module,arn=arn)
          module.exit_json(certificate=dict(domain_name=domain, arn=arn ), changed=True)
          
      else: 
        #create a new certificate
        arn = acm.import_certificate(client=client,
                                     module=module,
                                     certificate=module.params['certificate'],
                                     private_key=module.params['private_key'],
                                     certificate_chain=module.params['certificate_chain'],
                                     tags=tags)
        domain = acm.get_domain_of_cert(client=client,module=module,arn=arn)
      
        module.exit_json(certificate=dict(domain_name=domain, arn=arn ), changed=True)
      
    else: # state == absent
      for cert in certificates:
        acm.delete_certificate(client,module,cert['certificate_arn'])
      module.exit_json(arns=[cert['certificate_arn'] for cert in certificates],
                       changed=(len(certificates) > 0)) # TODO: return more info
    


if __name__ == '__main__':
    # tests()
    main()
