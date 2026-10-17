import { useEffect, useState } from 'react';
import {
  Anchor, Badge, Button, Card, Divider, Group, Modal, ScrollArea, SimpleGrid, Stack, Table, Text, Title,
} from '@mantine/core';
import { IconChevronDown, IconChevronRight, IconDeviceFloppy, IconExternalLink, IconTrash } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { useQueryClient } from '@tanstack/react-query';
import { applyDeviceSettings, deleteMiner, loadMinerGui, minerAction, saveMiner } from './api';
import { MinerFields } from './fields';
import { fieldsToValues, formForFields, validateFields, type MinerAction, type MinerDetail, type MinerModel, type MinerSummary, type MinerValues } from './model';
import { minerWebGuiUrl } from '../dashboard/minerLink';

function SummaryBadge({ text, state }: { text: string; state?: string }) {
  return <Badge size="sm" variant="light" radius="sm"
    color={state === 'ok' ? 'teal' : state === 'bad' ? 'red' : 'gray'}
    style={{textTransform:'none'}}>{text}</Badge>;
}

function ItemDetails({ items }: {items: MinerDetail[]}) {
  return <SimpleGrid cols={{base:2,sm:3,lg:5}} spacing="sm">
    {items.map((item, idx) => item.kind === 'table' ? (
      <div key={idx} style={{gridColumn:'1 / -1',minWidth:0}}>
        <Text size="xs" fw={600} mb="xs">{item.label}</Text>
        {(item.rows?.length ?? 0) ? <ScrollArea scrollbars="x" offsetScrollbars="x">
          <Table striped highlightOnHover miw={480} verticalSpacing="xs">
            <Table.Thead><Table.Tr>{(item.columns||[]).map(c=><Table.Th key={c.key}>{c.label}</Table.Th>)}</Table.Tr></Table.Thead>
            <Table.Tbody>{item.rows?.map((row,i)=><Table.Tr key={i}>
              {(item.columns||[]).map(c=><Table.Td key={c.key}><Text size="sm">{String(row[c.key]??'—')}</Text></Table.Td>)}
            </Table.Tr>)}</Table.Tbody>
          </Table>
        </ScrollArea> : <Text c="dimmed" size="sm">{item.empty || '—'}</Text>}
      </div>
    ) : (
      <div key={idx}>
        <Text size="xs" c="dimmed">{item.label}</Text>
        <Text size="sm" fw={600}>{String(item.value??'—')}</Text>
      </div>
    ))}
  </SimpleGrid>;
}

function showError(e: unknown) {
  notifications.show({color:'red',message:e instanceof Error ? e.message : 'Aktion fehlgeschlagen.'});
}

export function MinerCard({
  model, live, liveDetails, expanded, onExpandedChange,
}: {
  model: MinerModel;
  live?: MinerSummary;
  liveDetails?: MinerModel['details'];
  expanded: boolean;
  onExpandedChange: (expanded: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const allFields = [...model.identity_fields,...model.control_fields,...model.driver_fields];
  const [values,setValues] = useState<MinerValues>(()=>fieldsToValues(allFields));
  const [deviceValues,setDeviceValues] = useState<MinerValues>(()=>fieldsToValues(model.device_settings_fields));
  const [dirty,setDirty] = useState(false);
  const [deviceDirty,setDeviceDirty] = useState(false);
  const [busy,setBusy] = useState(false);
  const [pending,setPending] = useState<{kind:'action'|'delete'|'device';action?:MinerAction}|null>(null);
  const summary = live??model.summary;
  // Use the saved backend WebGUI target, never an uncommitted host edit.
  const webLink = minerWebGuiUrl(model.web_gui_url);

  useEffect(()=>{
    if (dirty || deviceDirty) return;
    setValues(fieldsToValues([...model.identity_fields,...model.control_fields,...model.driver_fields]));
    setDeviceValues(fieldsToValues(model.device_settings_fields));
  },[model,dirty,deviceDirty]);

  function change(field: {name:string},v:string|boolean) {
    setValues(old=>{
      const next={...old,[field.name]:v};
      if(field.name==='control_enabled' && v===true)next.monitor_enabled=true;
      if(field.name==='monitor_enabled' && v===false)next.control_enabled=false;
      return next;
    });
    setDirty(true);
  }
  async function refresh() {
    await queryClient.invalidateQueries({queryKey:['miners','gui']});
    await queryClient.invalidateQueries({queryKey:['miners','status']});
    await queryClient.invalidateQueries({queryKey:['dashboard']});
  }
  async function save() {
    if(!dirty || busy)return;
    const error=validateFields(allFields,values,true);
    if(error){notifications.show({color:'red',message:error});return;}
    setBusy(true);
    try {
      const form=formForFields(allFields,values);
      form.set('miner_id',model.id);
      const res=await saveMiner(model.id,form);
      notifications.show({color:'teal',message:res.message||'Miner gespeichert.'});
      setDirty(false);
      await refresh();
    }catch(e){showError(e);}finally{setBusy(false);}
  }
  async function executePending() {
    if(!pending || busy)return;
    const op=pending;
    setPending(null);
    setBusy(true);
    try {
      if(op.kind==='delete') {
        const res=await deleteMiner(model.id);
        notifications.show({color:'teal',message:res.message||'Miner gelöscht.'});
      }else if(op.kind==='device'){
        const form=new FormData();
        for(const field of model.device_settings_fields) {
          if(!field.value_known || field.read_only)continue;
          form.set('device__known__'+field.name,'1');
          if(field.type==='checkbox') {
            if(deviceValues[field.name]===true)form.set('device__'+field.name,'1');
          } else form.set('device__'+field.name,String(deviceValues[field.name]??''));
        }
        const res=await applyDeviceSettings(model.id,form);
        setDeviceDirty(false);
        notifications.show({color:'teal',message:res.message||'Geräteeinstellungen angewendet.'});
      }else if(op.action){
        const res=await minerAction(model.id,op.action.name);
        notifications.show({color:'teal',message:res.message||'Aktion ausgeführt.'});
      }
      await refresh();
    }catch(e){showError(e);}finally{setBusy(false);}
  }

  const deviceFields=model.device_settings_fields;
  return <Card component="section" aria-label={model.name} padding="md">
    <Stack gap="sm">
      <Group align="flex-start" justify="space-between" gap="xs">
        <div style={{minWidth:0}}>
          <Group gap="xs" wrap="wrap">
            <Text fw={650}>{model.name}</Text>
            <SummaryBadge text={summary.control_text} state={summary.control_class}/>
            <SummaryBadge text={summary.connection_text} state={summary.connection_class}/>
            {dirty && <Badge size="xs" color="orange" variant="light">Ungespeichert</Badge>}
          </Group>
          <Group gap="xs" mt={4}>
            <Text size="xs" c="dimmed">{model.driver_label}</Text>
            {webLink && <Anchor size="xs" href={webLink} target="_blank" rel="noopener noreferrer">
              WebGUI <IconExternalLink size={11} style={{verticalAlign:'middle'}}/>
            </Anchor>}
          </Group>
        </div>
        <Button variant="subtle" color="gray" size="compact-sm"
          rightSection={expanded?<IconChevronDown size={16}/>:<IconChevronRight size={16}/>}
          aria-expanded={expanded} onClick={()=>onExpandedChange(!expanded)}>
          {expanded?'Schließen':'Details'}
        </Button>
      </Group>
      <Group gap={5}>
        <SummaryBadge text={summary.runtime_state_text}/>
        <SummaryBadge text={summary.priority_text}/>
        {summary.profile_visible && <SummaryBadge text={summary.profile_text}/>}
        {summary.power_visible && <SummaryBadge text={summary.power_text}/>}
      </Group>
      {expanded && <Stack gap="md">
        {!model.supports_gui_schema ? <Text c="dimmed" size="sm">
          Der Treiber stellt noch kein vollständiges GUI-Schema bereit.
        </Text> : <>
          <Divider label="Identität" labelPosition="left"/>
          <MinerFields fields={model.identity_fields} values={values} disabled={busy} onChange={change}/>
          <Divider label="PV2Hash-Regelung" labelPosition="left"/>
          <MinerFields fields={model.control_fields} values={values} disabled={busy} onChange={change}/>
          <Divider label="Treiber-Konfiguration" labelPosition="left"/>
          <MinerFields fields={model.driver_fields} values={values} disabled={busy} onChange={change}/>
          <Group justify="flex-end" gap="xs">
            {dirty && <Button variant="subtle" color="gray" size="sm" disabled={busy} onClick={()=>{
              setValues(fieldsToValues(allFields));setDirty(false);
            }}>Verwerfen</Button>}
            <Button size="sm" leftSection={<IconDeviceFloppy size={16}/>}
              loading={busy} disabled={!dirty} onClick={()=>void save()}>Speichern</Button>
          </Group>
          {!!deviceFields.length && <>
            <Divider label="Geräte-Einstellungen" labelPosition="left"/>
            <Text c="dimmed" size="xs">Live-Werte des Geräts. Unbekannte Einstellungen bleiben gesperrt.</Text>
            <MinerFields fields={deviceFields} values={deviceValues} disabled={busy}
              onChange={(field,v)=>{setDeviceValues(old=>({...old,[field.name]:v}));setDeviceDirty(true);}}/>
            <Group justify="flex-end"><Button variant="light" size="sm" color="gray" disabled={!deviceDirty||busy}
              onClick={()=>setPending({kind:'device'})}>Geräte-Einstellungen anwenden</Button></Group>
          </>}
          {!!model.action_fields.length && <>
            <Divider label="Aktionen" labelPosition="left"/>
            <Group gap="xs">
              {model.action_fields.map(a=><Button key={a.name} size="sm"
                color={a.dangerous?'red':'gray'} variant="light"
                title={a.description||undefined}
                disabled={busy || (a.disabled_when_control_enabled && model.control_enabled)}
                onClick={()=>setPending({kind:'action',action:a})}>{a.label}</Button>)}
            </Group>
            {model.action_fields.map(a=>a.description?<Text key={a.name} size="xs" c="dimmed">
              {a.label}: {a.description}
            </Text>:null)}
          </>}
        </>}
        {((liveDetails ?? model.details)?.sections??[]).map((section,i)=><Card key={i} withBorder={false} bg="dark.7" padding="sm">
          <Title order={4} size="sm" mb="xs">{section.title}</Title>
          <ItemDetails items={section.items||[]}/>
        </Card>)}
        <Group justify="flex-end"><Button color="red" variant="subtle" size="sm"
          leftSection={<IconTrash size={16}/>} disabled={busy}
          onClick={()=>setPending({kind:'delete'})}>Miner löschen</Button></Group>
      </Stack>}
    </Stack>
    <Modal opened={!!pending} onClose={()=>setPending(null)} title="Aktion bestätigen" centered size="sm">
      <Stack gap="sm">
        <Text size="sm">{pending?.kind==='delete'
          ? `Miner „${model.name}“ wirklich löschen? Die Konfiguration wird entfernt.`
          : pending?.kind==='device' ? 'Geräteeinstellungen wirklich direkt auf dem Miner anwenden?'
          : pending?.action?.confirm_text || `Aktion „${pending?.action?.label}“ ausführen?`}</Text>
        <Group justify="flex-end">
          <Button variant="subtle" color="gray" onClick={()=>setPending(null)}>Abbrechen</Button>
          <Button color={pending?.kind==='delete'||pending?.action?.dangerous?'red':'violet'}
            disabled={busy} onClick={()=>void executePending()}>Bestätigen</Button>
        </Group>
      </Stack>
    </Modal>
  </Card>;
}
