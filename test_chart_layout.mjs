import test from 'node:test';
import assert from 'node:assert/strict';
import {StatsPanel} from './web/stats.js';
globalThis.document={documentElement:{lang:'en'}};

test('grouped bars leave room for both series at either plot edge',()=>{
  for(const clientWidth of [280,375,500,760])for(const count of [1,12,21,168]){
    const data=Array.from({length:count},(_,i)=>({ts:1700000000+i*600}));
    const f=StatsPanel.prototype.frame.call({period:'run'},data,16,{clientWidth,clientHeight:260},true);
    const gap=Math.min(4,f.band*.08),width=Math.min(14,(f.band*.8-gap)/2);
    assert.ok(f.x(0)-width-gap/2>f.L,'first group stays inside plot');
    assert.ok(f.x(count-1)+width+gap/2<f.W-f.R,'last group stays inside plot');
    assert.ok(width>0);
    assert.equal(f.y(0),f.H-f.B);
  }
});

const {chartIndex,tooltipPosition}=await import('./web/stats.js');
test('plot-wide selection resolves gaps, zero values, and chart edges',()=>{
  assert.equal(chartIndex(54,54,400,12),0);
  assert.equal(chartIndex(454,54,400,12),11);
  assert.equal(chartIndex(254,54,400,12),6);
  assert.equal(chartIndex(254,54,400,12,true),6);
  assert.equal(chartIndex(100,54,400,1),0);
  assert.equal(chartIndex(1000,54,400,12,true),11);
});
test('tooltip flips at the right edge and stays inside its host',()=>{
  for(const x of [0,50,250,450])for(const y of [0,100,260]){
    const {left,top}=tooltipPosition(x,y,180,85,470,275);
    assert.ok(left>=8&&left+180<=462);
    assert.ok(top>=8&&top+85<=267);
  }
  assert.ok(tooltipPosition(450,100,180,85,470,275).left<450);
});
