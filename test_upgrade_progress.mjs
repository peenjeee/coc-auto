import test from 'node:test';
import assert from 'node:assert/strict';
import {upgradeProgress, ongoingUpgrades} from './web/upgrade-progress.js';

const row={id:'cannon',name:'Cannon',current_level:1,next_level:2,section:'in_progress',seconds:15};

test('ongoing countdown and finish time use the sync timestamp, with estimated standard progress',()=>{
 const status=upgradeProgress(row,'buildings',100000,105000);
 assert.equal(status.remaining,10);
 assert.equal(status.finish,115000);
 assert.ok(Math.abs(status.percent-66.6666666667)<0.0001);
 assert.equal(status.estimated,true);
});

test('a recorded duration takes precedence and unknown totals do not invent percentages',()=>{
 assert.equal(upgradeProgress({...row,duration_seconds:60},'buildings',0,0).percent,75);
 assert.equal(upgradeProgress({...row,duration_seconds:60},'buildings',0,0).estimated,false);
 assert.equal(upgradeProgress({...row,id:'unknown'},'buildings',0,0).percent,null);
 assert.equal(upgradeProgress({...row,seconds:999999},'buildings',0,0).percent,null);
});

test('expired countdowns become ready to check; future timestamps never increase time left',()=>{
 const ready=upgradeProgress(row,'buildings',0,20000);
 assert.equal(ready.remaining,0);assert.equal(ready.ready,true);assert.equal(ready.percent,100);
 assert.equal(upgradeProgress(row,'buildings',10000,0).remaining,15);
});

test('all lists sort by expected finish without altering scan rows or queue targets',()=>{
 const target={id:'cannon',name:'Cannon',level:10};
 const lists={buildings:{rows:[row],scannedAt:1000,targets:[target]},lab:{rows:[{...row,seconds:10}],scannedAt:1000,targets:[]},pets:{rows:null,scannedAt:0,targets:[]}};
 const before=JSON.stringify(lists);const result=ongoingUpgrades(lists,2000);
 assert.deepEqual(result.map(item=>item.list),['lab','buildings']);
 assert.equal(JSON.stringify(lists),before);
});

test('invalid or non-running rows are excluded',()=>{
 for(const value of [{...row,section:'other'},{...row,seconds:NaN},{...row,seconds:-1},{...row,seconds:'15'}])assert.equal(upgradeProgress(value,'buildings',0,0),null);
 assert.equal(upgradeProgress(row,'buildings',NaN,0),null);
});
