import test from 'node:test';
import assert from 'node:assert/strict';
import {upgradeProgress, ongoingUpgrades} from '../../web/upgrade-progress.js';

const row={id:'cannon',name:'Cannon',current_level:1,next_level:2,section:'in_progress',seconds:15};

test('confirmed new upgrades use their start time instead of the stale sync clock',()=>{
 const updated={...row,current_level:37,next_level:38,receipt_id:5,started_at:1400,duration_seconds:72000,seconds:72400};
 const status=upgradeProgress(updated,'buildings',1000000,2000000);
 assert.equal(status.target,38);
 assert.equal(status.remaining,71400);
 assert.equal(status.finish,73400000);
 assert.equal(status.ready,false);
 assert.equal(status.estimated,false);
 assert.ok(status.percent>0&&status.percent<2);
});

test('confirmed upgrades with unknown duration remain active without inventing a finish time',()=>{
 const status=upgradeProgress({...row,receipt_id:5,started_at:1400,duration_seconds:null,seconds:null},'buildings',1000000,2000000);
 assert.equal(status.remaining,null);
 assert.equal(status.finish,null);
 assert.equal(status.percent,null);
 assert.equal(status.ready,false);
});

test('maximum-level research has estimated progress using its source-level upgrade duration',()=>{
 for (const [id,current_level,next_level,seconds,total] of [
   ['rage_spell',6,7,116717,1209600], ['root_rider',3,4,327224,1382400],
 ]) {
   const result=upgradeProgress({id,current_level,next_level,seconds,section:'in_progress'},'lab',100000,100000);
   assert.ok(result.percent>0&&result.percent<100,id);
   assert.equal(result.estimated,true);
   assert.ok(Math.abs(result.percent-100*(1-seconds/total))<1e-8,id);
 }
});

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
