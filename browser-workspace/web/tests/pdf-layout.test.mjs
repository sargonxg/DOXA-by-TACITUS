import assert from 'node:assert/strict';
import test from 'node:test';
import {readingOrder,omitRepeatedMargins,repeatedMargins} from '../src/pdf-layout.ts';

function fragment(text,x,y,index,width=220,height=10) {
  return {text,x,y,width,height,index,angle:0};
}

test('reads the left column before the right while preserving source fragments',()=>{
  const fragments=[];
  for(let row=0;row<5;row++) {
    fragments.push(fragment('Left '+row,40,100+row*15,row*2));
    fragments.push(fragment('Right '+row,330,100+row*15,row*2+1));
  }
  const result=readingOrder(fragments,612,792);
  assert.equal(result.columns,2);
  assert.deepEqual(result.lines.map(line=>line.text),[
    'Left 0','Left 1','Left 2','Left 3','Left 4',
    'Right 0','Right 1','Right 2','Right 3','Right 4',
  ]);
  assert.deepEqual(result.lines.flatMap(line=>line.fragments).sort((a,b)=>a-b),fragments.map(item=>item.index));
  assert(result.issues.includes('column_order_inferred'));
});

test('keeps an across-page heading and table rows in geometric order',()=>{
  const fragments=[fragment('Study heading',40,40,0,520,18)];
  for(let row=0;row<4;row++) {
    fragments.push(fragment('Group '+row,40,120+row*15,1+row*3,100));
    fragments.push(fragment(String(30+row),240,120+row*15,2+row*3,30));
    fragments.push(fragment(String(50+row),450,120+row*15,3+row*3,30));
  }
  const result=readingOrder(fragments,612,792);
  assert.equal(result.columns,1);
  assert.equal(result.lines[0].text,'Study heading');
  assert.deepEqual(result.lines.slice(1,4).map(line=>line.text),['Group 0','30','50']);
  assert.equal(result.tableRows,4);
  assert(result.issues.includes('table_layout'));
});

test('treats a wide PDF space as a column gutter without losing words',()=>{
  const fragments=[];
  for(let row=0;row<5;row++) {
    fragments.push(fragment('Left source wording '+row,40,100+row*15,row*3,200));
    fragments.push(fragment(' ',240,100+row*15,row*3+1,90));
    fragments.push(fragment('Right source wording '+row,330,100+row*15,row*3+2,220));
  }
  const result=readingOrder(fragments,612,792);
  assert.equal(result.columns,2);
  assert.deepEqual(result.lines.slice(0,5).map(line=>line.text),Array.from({length:5},(_,i)=>'Left source wording '+i));
});

test('suppresses repeated margins with placeholders retaining the line ordinal',()=>{
  const pages=[1,2,3,4].map(number=>({height:792,lines:readingOrder([
    fragment('DOXA RESEARCH',40,10,0,300),fragment('The methods describe the sample.',40,110,1,300),
    fragment(number+' / 4',540,770,2,30),
  ],612,792).lines}));
  const known=repeatedMargins(pages);
  const layout={lines:pages[0].lines,columns:1,readingOrder:'geometric_rows',tableRows:0,smallTextLines:0,issues:[]};
  const result=omitRepeatedMargins(layout,792,known);
  assert.deepEqual(result.lines.map(line=>line.text),['','The methods describe the sample.','']);
  assert.deepEqual(result.excluded.map(line=>line.line),[1,3]);
  assert.equal(result.lines[1].box[1],110);
  assert.equal(repeatedMargins(pages.slice(0,2)).size,0);
});

test('marks small bottom text for source inspection',()=>{
  const fragments=Array.from({length:8},(_,i)=>fragment('Body text '+i,40,100+i*20,i));
  fragments.push(fragment('1 A qualification in a footnote.',40,710,8,220,6));
  const result=readingOrder(fragments,612,792);
  assert.equal(result.smallTextLines,1);
  assert(result.issues.includes('small_text_or_footnotes'));
  assert(result.lines.some(line=>line.text==='1 A qualification in a footnote.'));
});

test('keeps native source order and warns on a complex three-column layout',()=>{
  const fragments=[];
  for(let column=0;column<3;column++) {
    for(let row=0;row<5;row++) fragments.push(fragment('Source column '+column+' has substantive wording '+row,35+column*195,100+row*15,column*5+row,150));
  }
  const result=readingOrder(fragments,612,792);
  assert.equal(result.columns,3);
  assert.equal(result.readingOrder,'native_content');
  assert(result.issues.includes('complex_column_layout'));
  assert(result.lines.slice(0,5).every(line=>line.text.startsWith('Source column 0')));
});
