import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { renderToken, validateExpressionTokens } from '../src/formula/model.ts';
import { readModbusFormula, validateModbusFormula } from '../src/sources/modbusFormulaModel.ts';

const registers = [
  {id:'ac',name:'AC',address:30775,register_type:'input',value_type:'int32',endian:'big_endian',factor:1},
  {id:'charge',name:'Laden',address:31393,register_type:'input',value_type:'uint32',endian:'big_endian',factor:1},
];
const variable=id=>({type:'register',id});
const numeric=value=>({type:'number',value});

test('formula editor accepts operands, constants, parentheses and all four operators', () => {
  const tokens=['(', variable('ac'), '+', variable('charge'), ')', '*', numeric(1.15), '-', numeric(-200), '/', numeric(2)];
  assert.equal(validateModbusFormula(JSON.stringify({registers,tokens})),null);
  assert.equal(renderToken(variable('charge'),registers),'Laden');
  assert.equal(renderToken(numeric(1.15),registers),'1,15');
});
test('requires >=2 configured and >=2 used registers', () => {
  assert.match(validateModbusFormula(JSON.stringify({registers:registers.slice(0,1),tokens:[variable('ac')]})),/Mindestens zwei/);
  assert.match(validateModbusFormula(JSON.stringify({registers,tokens:[variable('ac'),'+',numeric(4)]})),/mindestens zwei Register verwenden/);
});
test('rejects empty register names, duplicates and unbalanced expression', () => {
  assert.match(validateModbusFormula(JSON.stringify({registers:registers.map((r,i)=>i?{...r,name:'AC'}:r),tokens:[variable('ac')]})),/eindeutige Namen/);
  assert.match(validateModbusFormula(JSON.stringify({registers,tokens:['(',variable('ac'),'+',variable('charge')]})),/unvollständig/);
  assert.match(validateModbusFormula(JSON.stringify({registers,tokens:[variable('ac'),variable('charge')]})),/Operator/);
});
test('readFormula keeps JSON values and resets malformed drafts to blank editor', () => {
  const formula={registers,tokens:[variable('ac'),'+',variable('charge')]};
  assert.deepEqual(readModbusFormula(JSON.stringify(formula)),formula);
  assert.deepEqual(readModbusFormula('{oops'),{registers:[],tokens:[]});
});

test('independent formula grammar also accepts non-Modbus variables', () => {
  const example=[{type:'variable',id:'solar'},'*',{type:'number',value:0.8},'-',{type:'variable',id:'house'}];
  const result=validateExpressionTokens(example,new Set(['solar','house']));
  assert.equal(result.error,null);
  assert.deepEqual([...result.used].sort(),['house','solar']);
  assert.equal(renderToken(example[0],[{id:'solar',name:'PV'},{id:'house',name:'Verbrauch'}]),'PV');
});

test('invalid register data remains a validation error, never a renderer crash', () => {
  assert.match(validateModbusFormula(JSON.stringify({registers:[{...registers[0],name:123},registers[1]],tokens:[{type:'variable',id:'ac'},'+',{type:'variable',id:'charge'}]})),/eindeutige Namen|Formel/);
  assert.match(validateModbusFormula(JSON.stringify({registers:[{...registers[0],address:null},registers[1]],tokens:[{type:'variable',id:'ac'},'+',{type:'variable',id:'charge'}]})),/Registeradresse/);
});
