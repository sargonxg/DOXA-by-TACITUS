import type { EvidenceWindow, RankedWindow } from './claim-index';
import type { Verdict } from './verifier-math';
import type { StudyPacket } from './study-evidence';

export type ClaimCommand =
  | {kind:'check-excerpts'; claim:string; base:string; passages:EvidenceWindow[]}
  | {kind:'begin'; claim:string; base:string; pdfPages?:number}
  | {kind:'page'; page:number; lines:string[] | null}
  | {kind:'text'; file:File}
  | {kind:'finish'};

export type Lead = {
  window:RankedWindow;
  verdict:Verdict;
  rawScores:number[];
  abstract?:boolean;
};
export type ClaimResult = {
  method:'doxa-claim-selector-v1';
  claim:string;
  verdict:Verdict;
  scores:number[] | null;
  leads:Lead[];
  passages:number;
  shortlisted:number;
  screened:number;
  abstractChecked?:boolean;
  pdfPages?:number;
  unreadablePages:number;
  emptyPages:number;
  studyPacket?: StudyPacket;
};

export type ClaimEvent =
  | {kind:'ack'}
  | {kind:'status'; text:string}
  | {kind:'error'; text:string}
  | {kind:'result'; result:ClaimResult};
