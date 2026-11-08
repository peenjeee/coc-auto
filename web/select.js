// Styled dropdowns retain the select as the form's source of truth.
const controls = [];
let opened = null;
function close(focus = false) {
  if (!opened) return;
  const control = opened;
  opened = null;
  control.list.hidden = true;
  control.button.setAttribute('aria-expanded', 'false');
  if (focus) control.button.focus();
}
export function refreshSelects() {
  for (const c of controls) {
    c.button.disabled = c.select.disabled;
    c.value.textContent = c.select.selectedOptions[0]?.textContent || '—';
    c.list.setAttribute('aria-label', c.label);
    c.button.setAttribute('aria-label', `${c.label}: ${c.value.textContent}`);
    c.items.forEach((item, i) => {
      item.textContent = c.select.options[i].textContent;
      item.setAttribute('aria-selected', String(c.select.selectedIndex === i));
      item.disabled = c.select.options[i].disabled;
      item.hidden = c.select.options[i].hidden;
    });
  }
}
export function setupSelects() {
  for (const [index, select] of [...document.querySelectorAll('select:not([hidden])')].entries()) {
    const wrapper = document.createElement('span');
    wrapper.className = 'custom-select';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'select-trigger';
    button.setAttribute('role', 'combobox');
    button.setAttribute('aria-haspopup', 'listbox');
    button.setAttribute('aria-expanded', 'false');
    button.setAttribute('aria-controls', `select-list-${index}`);
    const value = document.createElement('span');
    button.append(value);
    const chevron = document.createElement('span');
    chevron.className = 'select-chevron'; chevron.setAttribute('aria-hidden','true');
    button.append(chevron);
    const list = document.createElement('span');
    list.id = `select-list-${index}`; list.className = 'select-list'; list.hidden = true;
    list.setAttribute('role', 'listbox');
    const labelNode = select.closest('label')?.querySelector('span');
    const c = {select, wrapper, button, value, list, items:[], get label() { return labelNode?.textContent || select.name; }};
    list.setAttribute('aria-label', c.label);
    const choose = i => {
      select.selectedIndex = i;
      select.dispatchEvent(new Event('change', {bubbles:true}));
      refreshSelects(); close(true);
    };
    const show = () => {
      close(); opened = c; list.hidden = false; button.setAttribute('aria-expanded','true');
      (c.items[select.selectedIndex] || c.items[0]).focus();
    };
    for (const [i, option] of [...select.options].entries()) {
      const item = document.createElement('button'); item.type = 'button'; item.setAttribute('role','option'); item.tabIndex=-1;
      item.addEventListener('click', e => {e.preventDefault(); choose(i);});
      item.addEventListener('keydown', e => {
        const enabled = c.items.filter(x=>!x.disabled&&!x.hidden); const at=enabled.indexOf(item);
        let next;
        if (e.key==='ArrowDown') next=enabled[(at+1)%enabled.length];
        if (e.key==='ArrowUp') next=enabled[(at-1+enabled.length)%enabled.length];
        if (e.key==='Home') next=enabled[0];
        if (e.key==='End') next=enabled[enabled.length-1];
        if (next) {e.preventDefault();next.focus();}
        else if (e.key==='Escape') {e.preventDefault();close(true);}
        else if (e.key==='Tab') close();
        else if (e.key.length===1 && e.key!==' ') {
          const match=enabled.find(x=>x.textContent.toLowerCase().startsWith(e.key.toLowerCase()));
          if(match) {e.preventDefault();match.focus();}
        }
      });
      list.append(item); c.items.push(item);
    }
    button.addEventListener('click', e => {e.preventDefault(); opened === c ? close() : show();});
    button.addEventListener('keydown', e => {
      if (['ArrowDown','ArrowUp','Home','End'].includes(e.key)) {e.preventDefault();show();}
      else if(e.key==='Escape') close(true);
    });
    select.before(wrapper); wrapper.append(select,button,list); select.hidden=true;
    select.addEventListener('change',refreshSelects); controls.push(c);
  }
  document.addEventListener('pointerdown', e => {if(opened && !opened.wrapper.contains(e.target))close();});
  document.addEventListener('focusin', e => {if(opened && !opened.wrapper.contains(e.target))close();});
  refreshSelects();
}
