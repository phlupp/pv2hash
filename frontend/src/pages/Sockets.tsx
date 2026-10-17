import {useState} from 'react';
import {Alert,Badge,Button,Group,Loader,Stack,Text,Title} from '@mantine/core';
import {IconAlertCircle,IconRefresh} from '@tabler/icons-react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {fetchSocketGui,fetchSocketStatus} from '../sockets/api';
import {SocketCard} from '../sockets/SocketCard';
import {SocketCreate} from '../sockets/SocketCreate';

export function Sockets(){
  const client=useQueryClient();
  const [busy,setBusy]=useState(false);
  const [expandedIds,setExpandedIds]=useState<string[]>([]);
  const gui=useQuery({queryKey:['sockets','gui'],queryFn:fetchSocketGui,staleTime:10_000});
  const live=useQuery({queryKey:['sockets','status'],queryFn:fetchSocketStatus,
    refetchInterval:5000,refetchIntervalInBackground:false});
  const byId=new Map((live.data?.sockets||[]).map(s=>[s.key,s]));
  async function refresh(){
    await Promise.all([
      client.invalidateQueries({queryKey:['sockets','gui']}),
      client.invalidateQueries({queryKey:['sockets','status']}),
    ]);
  }
  async function created(id?:string){
    if(id)setExpandedIds(ids=>[...new Set([...ids,id])]);
    await refresh();
  }
  return <Stack gap="md">
    <Group justify="space-between" align="center" gap="xs">
      <div>
        <Title order={2} size="h3">Sockets</Title>
        <Text c="dimmed" size="xs">Steckdosen und Relais · Simulator und Tasmota HTTP</Text>
      </div>
      <Group gap="xs">
        <Badge variant="light" color={live.isError?'red':'gray'} size="xs">
          {live.isError?'API-FEHLER':live.isPending?'Verbinde …':'API LIVE'}
        </Badge>
        <Button size="compact-sm" variant="subtle" color="gray" leftSection={<IconRefresh size={15}/>}
          disabled={busy} onClick={()=>void refresh()}>Aktualisieren</Button>
      </Group>
    </Group>
    {(gui.error||live.error)&&<Alert icon={<IconAlertCircle size={17}/>} color="red"
      title="Socket-API nicht erreichbar">{gui.error?.message||live.error?.message}</Alert>}
    {gui.isPending?<Group gap="sm"><Loader size="sm"/><Text c="dimmed">Socket-Modelle werden geladen …</Text></Group>
    :gui.data&&<>
      <SocketCreate drivers={gui.data.drivers} interfaces={gui.data.discovery_interfaces}
        busy={busy} onBusy={setBusy} onCreated={created}/>
      <Stack gap="sm">
        {gui.data.sockets.map(config=>{
          const driver=gui.data.drivers.find(d=>d.id===config.driver);
          if(!driver)return <Alert key={config.id} color="orange">Unbekannter Socket-Treiber: {config.driver}</Alert>;
          return <SocketCard key={config.id} config={config} driver={driver}
            drivers={gui.data.drivers} live={byId.get(config.id)}
            expanded={expandedIds.includes(config.id)} onExpandedChange={expanded=>setExpandedIds(ids=>
              expanded?[...new Set([...ids,config.id])]:ids.filter(id=>id!==config.id))}/>;
        })}
        {!gui.data.sockets.length&&<Text size="sm" c="dimmed">
          Noch keine Sockets konfiguriert. Über „Socket hinzufügen“ kannst du einen Simulator oder eine Tasmota-Steckdose anlegen.
        </Text>}
      </Stack>
    </>}
  </Stack>;
}
