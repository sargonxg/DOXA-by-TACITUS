"""Small, explicit synthetic practice inputs shipped inside the Python package.

These texts duplicate the published v0.5 practice inputs byte-for-byte. They are
not a human reference set, fresh inference or previously recorded model output.
"""
from copy import deepcopy

_FILES = [{'filename': 'company-message.txt',
  'profile_id': 'company',
  'text': 'Please review the draft before Friday. We will share the corrected customer notice tomorrow. '
          'Perhaps the support team can confirm which wording is still ambiguous.\n'
          '\n'
          "> A customer wrote: 'Your notice seems to suggest that every complaint is invalid.' This is "
          "quoted background, not the team's endorsement.\n"
          '\n'
          "Review the legal team's clarification before sending the notice. No customer notice is actually "
          'sent by this demonstration.',
  'sha256': '3f2941babb2447104dbef3255d9cd55ca6fbe982fcb27e8185ae002e42515f4c',
  'characters': 428,
  'data_origin': 'synthetic_demonstration'},
 {'filename': 'company-policy.txt',
  'profile_id': 'company',
  'text': 'This fictional company asks staff to state the requested action, name the responsible team where '
          'appropriate, and give a clear deadline. A direct request can be appropriate; qualification can be '
          'appropriate when information is incomplete.\n'
          '\n'
          'Quoted complaints and reports of discrimination must retain attribution. The policy does not '
          'authorize employee rankings, emotional diagnoses or inferences about religious or political '
          'beliefs.',
  'sha256': 'cb4795ba3357de71412a11dfed54e69f3a0ab7f500a62c84e0c2328b9350d029',
  'characters': 433,
  'data_origin': 'synthetic_demonstration'},
 {'filename': 'personal-thread.txt',
  'profile_id': 'personal',
  'text': 'Please send the revised figures before Friday. I think the dates in the summary may need '
          'clarification. We will review the final version together.\n'
          '\n'
          '> Previous message: Please confirm the old deadline.\n'
          '> The quoted request belongs to the previous message, not automatically to the current writer.\n'
          '\n'
          '--\n'
          'Fictional signature · no real email account is connected',
  'sha256': '1f025246ceb55e1c192612de2a43886b56ffc9f5f1dd60d66ec97d07392c74dd',
  'characters': 356,
  'data_origin': 'synthetic_demonstration'},
 {'filename': 'public-a.txt',
  'profile_id': 'public',
  'text': 'At a fictional city hearing, the housing committee described the proposed permit reform as a way '
          "to reduce delays. Its chair said, 'We will publish the underlying data before Monday.' A tenant "
          'group asked whether the reform would reduce available protections.\n'
          '\n'
          "A witness reported the slogan 'Muslims cannot be trusted.' The committee explicitly rejected that "
          'stereotype and affirmed that residents must be treated equally. Reporting and condemning this '
          'language is different from endorsing it.',
  'sha256': '80c645f00712f67d8d3671fe6f4298e502ca4f05834096e71895062e6960d0df',
  'characters': 493,
  'data_origin': 'synthetic_demonstration'},
 {'filename': 'public-b.txt',
  'profile_id': 'public',
  'text': 'A fictional tenant group framed the same permit proposal as a question of accountability. Its '
          "spokesperson said, 'Please provide the evidence before Monday.' The report also recorded the "
          "committee's argument that processing delays should be reduced.\n"
          '\n'
          "The report described criticism of the city's policy and condemnation of anti-Muslim stereotyping. "
          'These are different subjects. Possibly the selected hearing record is incomplete; this example '
          'supplies no independent evidence of deliberately omitted voices.',
  'sha256': '5978e03223e08e0833f888b0dacaa9a427726e865c18fa65871892245c0b7fda',
  'characters': 508,
  'data_origin': 'synthetic_demonstration'}]


def starter_pack():
    return {"id": "doxa-synthetic-starter-1", "title": "Five synthetic practice sources",
            "data_origin": "synthetic_demonstration", "license": "Apache-2.0",
            "model_requests": 0, "recorded_model_results": False, "independent_human_labels": False,
            "files": deepcopy(_FILES),
            "notice": "Fictional practice text for intake, context, wording and report workflows. No model interpretation or accuracy claim is supplied."}
