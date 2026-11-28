import type { Assessment, AssessmentCriterion } from './contracts';
import { claimLabels, claimLeads } from './research-claims';

export type DeskCase = {
  title: string; passage: string; context: string; note: string; revision: string;
  preservation: string; criterion: string; reference: string;
};
type Run = { text: string; context: string; example?: DeskCase };
type Action = { text: string; run: () => void } | { text: string; href: string };
type Options = {
  cases: Record<string,DeskCase>;
  screen: (input: Run) => Promise<Assessment>;
  openWorkbench: (input: Run) => void;
  openWording: (example: DeskCase) => void;
};

const byId = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const human = (s?: string | null) => (s ?? 'no decision').replaceAll('_',' ');
function element<K extends keyof HTMLElementTagNameMap>(tag: K, text?: string, css?: string) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  if (css) item.className = css;
  return item;
}
function summary(check: AssessmentCriterion) {
  const name = human(check.criterion_id).replace('derogation exclusion','derogation or exclusion');
  if (check.prediction_status === 'ok') return name + ' · ' + human(check.suggested_decision);
  return name + ' · ' + human(check.abstention_reason ?? check.reason ?? check.prediction_status);
}

export function mountDesk(options: Options) {
  const log = byId('desk-log');
  const form = byId<HTMLFormElement>('desk-form');
  const input = byId<HTMLTextAreaElement>('desk-input');
  const send = byId<HTMLButtonElement>('desk-send');
  const reset = byId<HTMLButtonElement>('desk-reset');
  const status = byId('desk-status');
  const label = byId('desk-label');
  const normalStatus = 'Local model · experimental screening';
  let last: Run | undefined;
  let lastPacket: Assessment | undefined;
  let contextFor: Run | undefined;
  let running = false;

  function actionsFor(parent: HTMLElement, links: Action[]) {
    const actions = element('div',undefined,'console-actions');
    for (const action of links) {
      if ('href' in action) {
        const link = element('a',action.text);
        link.href = action.href;
        actions.append(link);
      } else {
        const button = element('button',action.text,'console-chip');
        button.type = 'button';
        button.onclick = () => {
          if (running) return;
          action.run();
        };
        actions.append(button);
      }
    }
    if (actions.childElementCount) parent.append(actions);
  }
  function message(role: 'doxa' | 'user', text: string, actions: Action[] = []) {
    const node = element('article',undefined,'console-msg');
    node.dataset.role = role;
    node.append(element('h2',role === 'doxa' ? 'DOXA' : 'YOU','console-msg-header'));
    node.append(element('p',text));
    actionsFor(node,actions);
    log.append(node);
    reset.hidden = log.childElementCount <= 1;
    return node;
  }
  function follow(node: HTMLElement) {
    if (!log.closest<HTMLElement>('.view')?.hidden) node.scrollIntoView({block:'nearest'});
  }
  function prepare(context?: Run) {
    contextFor = context;
    input.placeholder = context ? 'Paste the surrounding context' : 'Ask a question or paste a passage';
    label.textContent = context ? 'Context to add' : 'Message to DOXA';
    input.focus();
  }
  function introduce() {
    log.replaceChildren();
    last = undefined;
    lastPacket = undefined;
    contextFor = undefined;
    input.value = '';
    label.textContent = 'Message to DOXA';
    input.placeholder = 'Ask a question or paste a passage';
    status.textContent = normalStatus;
    message('doxa','Paste political language to screen it locally, or try a short case with me.',[
      {text:'Try an example',run:chooseCase},
      {text:'Analyze a document',href:'#documents'},
      {text:'Check a claim',href:'#claims'},
      {text:'How it works',run:explain},
    ]);
  }
  function chooseCase() {
    follow(message('doxa','Pick a fictional case. We’ll run the passage first, then compare it with the supplied context.',[
      {text:'Quotation or endorsement?',run:() => void demo(options.cases.quote)},
      {text:'Criticism or collective blame?',run:() => void demo(options.cases.blame)},
      {text:'What do the numbers support?',run:() => void demo(options.cases.evidence)},
      {text:'A claim in conflict reporting',run:() => void demo(options.cases.conflict)},
    ]));
  }
  function explain() {
    follow(message('doxa','The guide explains this workflow. The trained model screens each supported criterion in your browser. Its suggestions and uncalibrated scores are prompts for review. Check the passage and speaker before drawing a conclusion.',[
      {text:'See the model’s results',href:'#results'},
      {text:'Inspect the rubric',href:'#methodology'},
    ]));
  }
  function reference(run: Run) {
    if (!run.example) {
      follow(message('doxa','The live model does not provide a verified evidence quote. Open the workbench to mark the words yourself and keep your reading alongside the model’s suggestions.',[
        {text:'Mark evidence',run:() => options.openWorkbench(run)},
      ]));
      return;
    }
    const node = message('doxa','Full case annotation · ' + run.example.criterion,[
      {text:'Compare an authored revision',run:() => options.openWording(run.example!)},
      {text:'Browse reference cases',href:'#explore'},
    ]);
    node.append(element('blockquote',run.example.reference),element('p',run.example.note));
    follow(node);
  }
  async function demo(example: DeskCase) {
    message('user',example.passage);
    await analyze({text:example.passage,context:'',example});
  }
  async function analyze(run: Run) {
    if (running) return;
    if (![run.text,run.context].every(value => [...value].length <= 6000)) {
      follow(message('doxa','Use up to 6,000 Unicode characters in the passage and in its context.'));
      return;
    }
    running = send.disabled = reset.disabled = true;
    status.textContent = 'Running the model locally…';
    try {
      const packet = await options.screen(run);
      const before = last?.text === run.text && last.context !== run.context ? lastPacket : undefined;
      last = run;
      lastPacket = packet;
      const supported = packet.criteria.filter(check => check.prediction_status === 'ok');
      const flagged = supported.filter(check => check.suggested_decision === 'present');
      const uncertain = supported.filter(check => check.suggested_decision === 'insufficient_context');
      const undecided = packet.criteria.length - supported.length;
      let reply: string;
      if (!supported.length) {
        reply = 'The model did not return a supported decision for this passage.';
      } else if (!flagged.length) {
        reply = 'The model raised no positive flags. You can inspect the checks and add context.';
      } else {
        reply = 'The model flagged ' + flagged.map(check => human(check.criterion_id)).join(', ')
          + ' for review. Check the speaker and surrounding text before accepting the suggestion.';
      }
      if (uncertain.length || undecided) {
        const unable = [];
        if (uncertain.length) unable.push(uncertain.length + ' requested more context');
        if (undecided) unable.push(undecided + ' returned no decision');
        reply += ' Of the ' + packet.criteria.length + ' checks, ' + unable.join(' and ') + '.';
      }
      const actions: Action[] = [];
      if (run.example && !run.context) {
        const next = { ...run, context:run.example.context };
        actions.push({text:'Run with supplied context',run:() => {
          message('user',next.context);
          void analyze(next);
        }});
      }
      actions.push({text:'Add my context',run:() => prepare(run)});
      actions.push({text:run.example ? 'Show the annotated evidence' : 'Review evidence',run:() => reference(run)});
      const node = message('doxa',reply + (run.context ? ' This run used the supplied context.' : ''));
      if (before) {
        const prior = new Map(before.criteria.map(check => [check.criterion_id,summary(check)]));
        const changes = packet.criteria.filter(check => prior.get(check.criterion_id) !== summary(check));
        node.append(element('p',changes.length
          ? 'Changed checks after adding context · ' + changes.map(summary).join(' · ') + '.'
          : 'The model returned the same decisions after the context change.'));
      }
      const leads=claimLeads(run.text);
      if (leads.length) {
        const evidence=element('details');
        evidence.append(element('summary',`Inspect causal wording (${leads.length})`));
        for (const {kind,start,end} of leads.slice(0,6)) {
          evidence.append(element('p',claimLabels[kind]),element('blockquote',run.text.slice(start,end)));
        }
        node.append(evidence);
      }
      const checks = element('details');
      checks.append(element('summary','Show all checks'));
      const list = element('ul');
      for (const item of packet.criteria) list.append(element('li',summary(item)));
      checks.append(list);
      node.append(checks);
      actionsFor(node,actions);
      if (!supported.length) {
        node.dataset.result = 'no-decision';
      } else {
        node.dataset.result = 'screened';
      }
      follow(node);
    } catch (error) {
      follow(message('doxa',error instanceof Error ? error.message : 'The local model could not finish. Try again.'));
    } finally {
      running = send.disabled = reset.disabled = false;
      status.textContent = normalStatus;
    }
  }
  function takeMessage(value: string) {
    const context = contextFor;
    contextFor = undefined;
    input.placeholder = 'Ask a question or paste a passage';
    label.textContent = 'Message to DOXA';
    message('user',value);
    if (context) {
      void analyze({...context,context:value});
      return;
    }
    const prompt = value.toLowerCase().replace(/[?!.]+$/,'').trim();
    if (/^(?:\/?(?:example|demo)|try an example|show (?:me )?(?:an |a )?(?:example|case))$/.test(prompt)) {
      chooseCase();
    } else if (/^(?:how (?:does (?:this|doxa|it) work|do i use (?:this|doxa))|what (?:is|can) doxa(?: do)?|help|\/help|what do you check)$/.test(prompt)
        || /^(?:why|what|how)\b.*\b(?:flag|score|decision|result)/.test(prompt)) {
      explain();
    } else if (/^(?:show (?:me )?(?:the )?(?:evidence|source)|\/evidence)$/.test(prompt)) {
      if (last) reference(last);
      else follow(message('doxa','Run a passage or try a case first.',[{text:'Try an example',run:chooseCase}]));
    } else if (/^(?:check|verify) (?:a |this )?claim\b/.test(prompt) || /^\/?claim(?: check)?$/.test(prompt)) {
      follow(message('doxa','Open Claim Check to compare one claim with a PDF or text file. DOXA will show the passage it used.',[
        {text:'Open Claim Check',href:'#claims'},
      ]));
    } else if (/^(?:review|analy[sz]e|open|read)\b.*\b(?:paper|pdf|document)\b/.test(prompt) || prompt==='/document') {
      follow(message('doxa','Open the Document Desk to review a PDF, text file or paper. It will point you back to the passages that raised each check.',[
        {text:'Open Document Desk',href:'#documents'},
      ]));
    } else if (/^(?:open (?:the )?conflict desk|\/conflict|conflict desk)$/.test(prompt)) {
      follow(message('doxa','The Conflict Desk has source packets and media-coded records for investigating claims.',[{text:'Open Conflict Desk',href:'#conflict'}]));
    } else if (/^(?:\/?scorecard|open (?:the )?scorecard)$/.test(prompt)) {
      follow(message('doxa','The Scorecard shows the numerator, denominator and exclusions for a matched-claim audit.',[{text:'Open Scorecard',href:'https://huggingface.co/spaces/Giuliocc/doxa-scorecard'}]));
    } else if (/^(?:analyze|analyse|screen)(?: my (?:text|passage))?$/.test(prompt)) {
      follow(message('doxa','Paste the passage in the message field. You can add its context after the first run.'));
      prepare();
    } else if (!/^analy[sz]e:/i.test(value) && value.endsWith('?')) {
      follow(message('doxa','I can guide a passage review, show the source behind an example or explain how the checks work.',[
        {text:'Try an example',run:chooseCase},{text:'How it works',run:explain},
      ]));
    } else {
      const body = value.replace(/^analy[sz]e:\s*/i,'');
      const separator = /\ncontext:\s*/i.exec(body);
      const text = separator ? body.slice(0,separator.index) : body;
      const contextText = separator ? body.slice(separator.index + separator[0].length) : '';
      if (text.trim()) void analyze({text:text.trim(),context:contextText.trim()});
      else follow(message('doxa','Add the passage you want me to screen.'));
    }
  }
  form.onsubmit = event => {
    event.preventDefault();
    const value = input.value.trim();
    if (!value || running) return;
    if ([...value].length > 12_050) {
      status.textContent = 'This message is too long. Limit the passage and context to 6,000 characters each.';
      return;
    }
    input.value = '';
    takeMessage(value);
  };
  input.addEventListener('keydown',event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  reset.onclick = () => { introduce(); input.focus(); };
  introduce();
}
