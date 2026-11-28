import { loadModel, predict } from './inference';
import { quoteGroups } from './corpus';
import type { Assessment, Processed, WorkerCommand, WorkerReport } from './contracts';

let model: Awaited<ReturnType<typeof loadModel>> | undefined;
let canceled = false;
let active = false;
const emit = (report: WorkerReport) => self.postMessage(report);
const pause = () => new Promise<void>(resolve => setTimeout(resolve, 0));
const normalizeForGrouping = (text: string) => text.normalize('NFC').replace(/\s+/gu,' ').trim();

async function start(command: WorkerCommand) {
  if (command.kind === 'cancel') { canceled = true; return; }
  if (active) return;
  active = true;
  canceled = false;
  try {
    if (!model) {
      model = await loadModel(command.modelUrl);
      emit({ kind: 'loaded' });
    }
    if (command.kind === 'analyze') {
      emit({ kind: 'analysis', packet: predict(model, command.text, command.context) as unknown as Assessment });
      return;
    }
    const repeatedText = new Map<string, Processed>();
    const quotes = new Map<string, string>();
    const total = command.inputs.length;
    for (const [index, input] of command.inputs.entries()) {
      if (canceled) break;
      const key = JSON.stringify([normalizeForGrouping(input.text), normalizeForGrouping(input.context)]);
      const earlier = repeatedText.get(key);
      const quoteLines = quoteGroups(input.text);
      const repeated = quoteLines.filter(quote => quotes.has(quote));
      const row: Processed = {
        input, group: input.thread_id || input.document_id,
        repeated_quotes: repeated.map(quote => quotes.get(quote)!),
        reviews: {},
      };
      if (input.skip_reason) row.skip_reason = input.skip_reason;
      else if (earlier) {
        row.duplicate_of = earlier.input.record_id;
        row.group = earlier.group;
        row.skip_reason = 'Text and context duplicate an earlier record.';
      } else {
        row.assessment = predict(model, input.text, input.context) as unknown as Assessment;
        repeatedText.set(key, row);
        quoteLines.forEach(quote => { if (!quotes.has(quote)) quotes.set(quote, input.record_id); });
      }
      emit({ kind: 'progress', result: row, done: index + 1, total });
      await pause();
    }
    emit({ kind: 'complete', canceled });
  } catch {
    emit({ kind: 'error', message: 'The bundled model could not complete this local operation. The reference cases are still available.' });
  } finally { active = false; }
}
self.addEventListener('message', (event: MessageEvent<WorkerCommand>) => { void start(event.data); });
