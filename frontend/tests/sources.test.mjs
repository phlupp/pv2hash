import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import {
  flattenFields, formValuesFromModels, toFormData, validateSourceValues, modelsFromPayload, isFiveFieldUniformGroup,
} from '../src/sources/model.ts';

const header = { status: { state: 'live', text: 'Live' }, header_fields: [], detail_groups: [], config_fields: [], driver_field: null };

test('generic model accepts future PV role without explicit component/role changes', () => {
  const models = [
    {...header, id: 'grid', role: 'grid', title: 'Netz', driver: 'simulator'},
    {...header, id: 'battery', role: 'battery', title: 'Batterie', driver: 'battery_modbus'},
    {...header, id: 'pv_1', role: 'pv', title: 'PV-Erzeugung', driver: 'inverter'},
  ];
  assert.deepEqual(modelsFromPayload({ status: 'ok', gui_models: models }).map(m => m.id), ['grid', 'battery', 'pv_1']);
});

test('nested Modbus fieldsets yield concrete fields including optional registers', () => {
  const fields = [
    { type: 'fieldset', label: 'SOC', fields: [
      { name: 'soc_address', type: 'number', value: 3200, required: true },
      { name: 'soc_factor', type: 'number', value: 0.01 },
    ]},
    { name: 'battery_enabled', type: 'checkbox', value: true },
  ];
  assert.deepEqual(flattenFields(fields).map(f => f.name), ['soc_address', 'soc_factor', 'battery_enabled']);
  const values = formValuesFromModels([{ ...header, id:'battery',title:'Batterie',driver:'modbus', config_fields:fields }]);
  assert.equal(values.soc_factor, '0.01');
  assert.equal(values.battery_enabled, true);
  const form = toFormData(values);
  assert.equal(form.get('battery_enabled'), 'on');
  assert.equal(form.get('soc_address'), '3200');
  assert.equal(form.get('soc_factor'), '0.01');
});

test('validation blocks missing required register, bad numeric values and inactive fields', () => {
  const model = {...header, id:'battery',title:'Batterie',driver:'battery_modbus',config_fields:[
    {name:'battery_enabled',type:'checkbox',value:true},
    {name:'battery_host',type:'text',value:'10.1.2.3',required:true},
    {name:'battery_port',type:'number',value:502,min:1,max:65535,required:true},
    {name:'optional_address',type:'number',value:null,required:false},
    {name:'disabled_in_none',type:'text',value:'',required:true,disabled_when_driver:'battery_modbus'},
  ]};
  const base=formValuesFromModels([model]);
  assert.deepEqual(validateSourceValues([model],base), []);
  assert.match(validateSourceValues([model],{...base,battery_host:''})[0],/Batterie.*battery_host/);
  assert.match(validateSourceValues([model],{...base,battery_port:'abc'})[0],/Ungültiger Zahlenwert/);
  assert.match(validateSourceValues([model],{...base,battery_port:'90000'})[0],/Ungültiger Zahlenwert/);
  assert.equal(toFormData({...base,battery_enabled:false}).has('battery_enabled'), false);
});

test('uniform five-field layouts use a dense row; mixed widths keep driver hints', () => {
  const fiveFields = ['address', 'register_type', 'value_type', 'endian', 'factor'].map(name => ({
    name, type: 'text', layout: { width: 'quarter' },
  }));
  assert.equal(isFiveFieldUniformGroup(fiveFields), true);
  assert.equal(isFiveFieldUniformGroup(fiveFields.slice(0, 4)), false);
  assert.equal(isFiveFieldUniformGroup(fiveFields.map((f,i) => i === 4 ? {...f,layout:{width:'half'}} : f)), false);
});
