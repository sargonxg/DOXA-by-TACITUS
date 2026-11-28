import { words } from './claim-index.ts';
import type { RankedWindow } from './claim-index.ts';

export const labels = ['supported','contradicted','insufficient_context'] as const;
export type Verdict = typeof labels[number];
export type MODEL = {
  classes: Verdict[]; coefficients: number[][]; intercept: number[]; mean: number[]; scale: number[];
  indices?: number[];
};
export type ScoredWindow = RankedWindow & {logits: number[]};

export function softmax(values: number[]) {
  const peak = Math.max(...values);
  const exp = values.map(x=>Math.exp(x-peak));
  const total = exp.reduce((a,b)=>a+b,0);
  return exp.map(x=>x/total);
}

export function output(model: MODEL, features: number[]) {
  const chosen = model.indices ? model.indices.map(i=>features[i]) : features;
  const z = chosen.map((x,i)=>(x-model.mean[i])/model.scale[i]);
  const probabilities = softmax(model.coefficients.map((row,i)=>
    row.reduce((value,coefficient,j)=>value+coefficient*z[j],model.intercept[i])));
  return labels.map(key=>probabilities[model.classes.indexOf(key)]);
}

export function best(values: number[]) {
  return values.indexOf(Math.max(...values));
}

function overlap(q: Set<string>, p: Set<string>) {
  return [...q].filter(x=>p.has(x)).length / Math.max(1,q.size);
}

export function pairFeatures(premise: string, claim: string, logits: number[]) {
  const left = words(premise);
  const right = words(claim);
  const bigrams = (tokens: string[]) => new Set(tokens.slice(1).map((x,i)=>tokens[i]+' '+x));
  const numbers = (text: string) => new Set((text.match(/(?<![\p{L}\p{N}_.])\d+(?:[,\d]*)(?:\.\d+)?(?![\p{L}\p{N}_.])/gu) ?? [])
    .map(num=>num.replaceAll(',','')));
  const a = numbers(premise);
  const b = numbers(claim);
  return [...logits,overlap(new Set(right),new Set(left)),overlap(bigrams(right),bigrams(left)),
    overlap(b,a),Number([...b].some(x=>!a.has(x))),Math.log1p(right.length),Math.log1p(left.length)];
}

export function summarize(windows: ScoredWindow[], claim: string, original: MODEL) {
  const top = windows[0].relevance;
  const rows = windows.map(window=>{
    const adapter = output(original,pairFeatures(window.text,claim,window.logits));
    const raw = softmax(window.logits);
    const relative = window.relevance/top;
    return {window,adapter,raw,relative};
  });
  const leads = labels.map((_,i)=>{
    const strength = rows.map(row=>(.6*row.adapter[i]+.4*row.raw[i])*(.55+.45*row.relative));
    return {row:rows[best(strength)],strength:Math.max(...strength)};
  });
  const strongest = rows[best(rows.map(row=>Math.max(row.adapter[0],row.adapter[1])))];
  const topAdapter = rows[0].adapter;
  const weighted = labels.map((_,i)=>rows.reduce((s,row)=>s+row.adapter[i]*row.relative,0)/
    rows.reduce((s,row)=>s+row.relative,0));
  const features = [...topAdapter,...rows[0].raw,...leads.map(x=>x.strength),
    ...weighted,...leads.slice(0,2).map(x=>x.row.relative)];
  return {features,leads,topAdapter,strongest};
}

export function corroborates(original:MODEL, passage:string, claim:string, logits:number[], target:number) {
  if (target===2) return false;
  const likelihood=softmax(logits);
  const decision=best(output(original,pairFeatures(passage,claim,logits)));
  return decision===target || (best(likelihood)===target && likelihood[target]>=.75);
}
