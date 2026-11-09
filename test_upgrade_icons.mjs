import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {UPGRADE_ICONS, UPGRADE_LEVEL_ICONS, UPGRADE_ALIASES} from './web/upgrade-icons.js';
import {SPRITE_TILE, SPRITE_SHEETS, UPGRADE_SPRITES} from './web/upgrade-sprites.js';
const web = new URL('./web/',import.meta.url);

test('all six sheets contain their mapped portraits with bounds and provenance',()=>{
  const sources=JSON.parse(fs.readFileSync(new URL('icons/entities/sources.json',web),'utf8'));
  assert.equal(Object.keys(SPRITE_SHEETS).length,6);
  for(const list of Object.keys(UPGRADE_ICONS)) {
    const paths=new Set([...Object.values(UPGRADE_ICONS[list]),...Object.values(UPGRADE_LEVEL_ICONS[list]).flatMap(Object.values)]);
    const [sheet,width,height]=SPRITE_SHEETS[list];
    const file=fs.readFileSync(new URL('icons/sprites/'+sheet,web));
    assert.equal(file.subarray(0,4).toString(),'RIFF');
    assert.equal(file.subarray(8,12).toString(),'WEBP');
    for(const path of paths){
      const frame=UPGRADE_SPRITES[list][path];
      assert.ok(frame,`${list}/${path}`);
      const [x,y]=frame;
      assert.ok(x>=0&&y>=0&&x+SPRITE_TILE<=width&&y+SPRITE_TILE<=height,path);
      assert.ok(sources.assets[path.replace('icons/entities/','')],path);
      assert.equal(upgradePortraitSprite(path,list).src,'icons/sprites/'+sheet);
    }
  }
});

test('all upgrade lists have portraits, with distinct Home and Builder Base artwork',()=>{
  assert.deepEqual(Object.keys(UPGRADE_ICONS),['buildings','lab','pets','gear','builder','star']);
  assert.ok(UPGRADE_ICONS.buildings.barbarian_king);
  assert.ok(UPGRADE_ICONS.lab.valkyrie);
  assert.ok(UPGRADE_ICONS.pets.lassi);
  assert.ok(UPGRADE_ICONS.gear.giant_gauntlet);
  assert.ok(UPGRADE_ICONS.builder.battle_machine);
  assert.ok(UPGRADE_ICONS.star.cannon_cart);
  assert.notEqual(UPGRADE_ICONS.buildings.archer_tower,UPGRADE_ICONS.builder.archer_tower);
  assert.equal(UPGRADE_ALIASES.buildings.rajabarbar,'barbarian_king');
});

import {upgradePortraitSource, upgradePortraitFallback, upgradePortraitSprite} from './web/upgrade-portrait.js';
test('export names resolve refreshed artwork and the legacy Raging Barbarian ID',()=>{
 for(const [list,id] of [['lab','meteor_golem'],['lab','sky_wagon'],['pets','sneezy'],['gear','rocket_spear'],['gear','frozen_arrow'],['builder','b_o_b_control'],['star','raging_barbarian']]){
   const path=upgradePortraitSource(list,{id});
   assert.ok(path,`${list}/${id}`);
   assert.ok(fs.existsSync(new URL(upgradePortraitSprite(path,list).src,web)),path);
 }
 assert.match(upgradePortraitSource('star',{id:'raging_barbarian'}),/raged_barbarian/);
 assert.match(upgradePortraitSource('star',{name:'Raged Barbarian'}),/raged_barbarian/);
 assert.match(upgradePortraitSource('buildings',{id:'revenge_tower',current_level:2}),/revenge_tower__level_2/);
});
test('missing exact levels and unknown entities get generic icons without changing real levels',()=>{
 const row={id:'blacksmith',data_id:1000070,current_level:10};
 assert.equal(upgradePortraitSource('buildings',row),null);
 assert.equal(upgradePortraitFallback('buildings',row),'icons/upgrades/equipment.webp');
 assert.equal(row.current_level,10);
 for(const list of ['buildings','builder','lab','star','pets','gear']){
   const path=upgradePortraitFallback(list,{name:'New item'});
   assert.ok(fs.existsSync(new URL(path,web)),path);
 }
 assert.match(upgradePortraitSource('buildings',{id:'blacksmith',current_level:9}),/level_9/);
});
test('export portraits use the actual current level while explicit queue goals stay independent',()=>{
 const row={id:'archer_tower',name:'Archer Tower',source:'game_export',current_level:7,next_level:8};
 assert.match(upgradePortraitSource('buildings',row),/level_7\.webp$/);
 assert.match(upgradePortraitSource('buildings',{...row,level:14},true,[row]),/level_14\.webp$/);
 assert.match(upgradePortraitSource('buildings',{...row,level:null},true,[row]),/level_7\.webp$/);
});
test('building artwork follows the displayed destination level and queue goal',()=>{
 const row={id:'archer_tower',name:'Archer Tower',next_level:8};
 assert.match(upgradePortraitSource('buildings',row),/level_8\.webp$/);
 assert.match(upgradePortraitSource('buildings',{name:'Menara Pemanah',next_level:8}),/level_8\.webp$/);
 assert.match(upgradePortraitSource('buildings',{...row,level:14},true,[row]),/level_14\.webp$/);
 assert.match(upgradePortraitSource('buildings',{...row,level:null},true,[row]),/level_8\.webp$/);
 assert.match(upgradePortraitSource('builder',row),/builder-base.*level_8\.webp$/);
 assert.equal(upgradePortraitSource('buildings',{...row,next_level:null}),UPGRADE_ICONS.buildings.archer_tower);
 assert.equal(upgradePortraitSource('buildings',{...row,next_level:999}),null);
 assert.equal(upgradePortraitSource('buildings',{id:row.id,name:row.name,level:null},true,[]),UPGRADE_ICONS.buildings.archer_tower);
 assert.equal(upgradePortraitSource('buildings',{id:'eagle_artillery',name:'Eagle Artillery',next_level:null}),UPGRADE_ICONS.buildings.eagle_artillery);
 assert.ok(upgradePortraitSource('buildings',{id:'barbarian_king',name:'Barbarian King',next_level:11}).endsWith('icon.webp'));
});


test('sprite frames never overlap and all pinned sources remain covered', () => {
 const provenance=JSON.parse(fs.readFileSync(new URL('icons/entities/sources.json',web),'utf8'));
 const keys=new Set(Object.values(UPGRADE_SPRITES).flatMap(Object.keys));
 assert.deepEqual([...keys].sort(),Object.keys(provenance.assets).map(name=>'icons/entities/'+name).sort());
 for(const entries of Object.values(UPGRADE_SPRITES)){
  const frames=[];
  for(const [key,[x,y]] of Object.entries(entries)){
   for(const frame of frames)assert.ok(x+SPRITE_TILE<=frame.x||frame.x+SPRITE_TILE<=x||y+SPRITE_TILE<=frame.y||frame.y+SPRITE_TILE<=y,key);
   frames.push({x,y});
  }
 }
 assert.equal(upgradePortraitSprite('missing','buildings'),null);
 assert.equal(upgradePortraitSprite('missing','unknown'),null);
});
