import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

// Windows checkouts use CRLF; the line-based import stripping below expects LF.
const source = (await fs.readFile(new URL('../../web/settings.js', import.meta.url), 'utf8')).replace(/\r\n/g, '\n');
function fixture() {
  const nodes = new Map();
  const make = id => ({id, value:'', textContent:'', disabled:false, children:[], listeners:{},
    addEventListener(name,fn){this.listeners[name]=fn;},
    setAttribute(name,value){this[name]=value;},
    replaceChildren(){this.children=[];},append(child){this.children.push(child);},
    elements:{fields:new Map(),namedItem(name){if(!this.fields.has(name))this.fields.set(name,make(name));return this.fields.get(name);}}});
  const document = {getElementById(id){if(!nodes.has(id))nodes.set(id,make(id));return nodes.get(id);},createElement:make};
  const calls=[];
  const context=vm.createContext({document,t:key=>key,translate:text=>text});
  vm.runInContext(source.replace(/^import .+;\n/gm,'').replace('export class SettingsPanel','class SettingsPanel')+
    '\nglobalThis.SettingsPanel=SettingsPanel;',context);
  const panel=new context.SettingsPanel((...args)=>calls.push(args));
  return {panel,nodes,calls};
}

test('checking the village invalidates a previous success until the new reply arrives',()=>{
  const {panel,nodes,calls}=fixture();
  panel.checkedSetup({ready:true});assert.equal(nodes.get('setup-continue').disabled,false);
  nodes.get('setup-check').listeners.click();
  assert.equal(nodes.get('setup-continue').disabled,true);
  assert.equal(calls[0][0],'check_setup');
  panel.checkedSetup({ready:false});assert.equal(nodes.get('setup-continue').disabled,true);
});

test('instance choices are explicit and switching device invalidates readiness',()=>{
  const {panel,nodes,calls}=fixture();
  panel.detected({ld_index:1,instances:[{index:0,name:'First',running:true},{index:1,name:'Second',running:false}],problems:[]});
  const choices=nodes.get('setup-instances').children;
  assert.equal(choices.length,2);assert.equal(choices[1]['aria-checked'],'true');
  panel.checkedSetup({ready:true});
  choices[0].listeners.click();
  assert.equal(calls.at(-1)[0],'detect_setup');assert.equal(calls.at(-1)[1].index,0);
  panel.detected({ld_index:0,instances:[],problems:[]});
  assert.equal(nodes.get('setup-continue').disabled,true);
});

test('unrelated commands cannot enable Continue before a successful village check',()=>{
  const {panel,nodes}=fixture();
  panel.busy(true); panel.busy(false);
  assert.equal(nodes.get('setup-continue').disabled,true);
  panel.checkedSetup({ready:true}); panel.busy(true);
  assert.equal(nodes.get('setup-continue').disabled,true);
  panel.busy(false);assert.equal(nodes.get('setup-continue').disabled,false);
});

test('a failed detection clears stale fields from the previously selected emulator',()=>{
  const {panel,nodes}=fixture();
  panel.detected({ld_index:0,serial:'emulator-5554',touch_device:'/dev/input/event4',adb:'adb.exe',ldconsole:'ldconsole.exe'});
  assert.equal(nodes.get('setup-form').elements.namedItem('serial').value,'emulator-5554');
  panel.detected({ld_index:2,problems:['stopped']});
  assert.equal(nodes.get('setup-form').elements.namedItem('serial').value,'');
  assert.equal(nodes.get('setup-save').disabled,true);
});
