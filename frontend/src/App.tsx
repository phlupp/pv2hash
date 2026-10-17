import { lazy, Suspense, useState } from 'react';
import {
  ActionIcon, AppShell, Badge, Box, Burger, Center, Divider, Group, Image, Loader, NavLink,
  ScrollArea, Stack, Text, Tooltip
} from '@mantine/core';
import { useDisclosure, useMediaQuery } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import {
  IconActivity, IconChartBar, IconChevronLeft, IconChevronRight, IconCloud,
  IconCpu, IconGauge, IconPlugConnected, IconSettings, IconTerminal2,
  IconWand, IconZoomCheck
} from '@tabler/icons-react';
import { getStatus, getVersion } from './api';
import { version as guiVersion } from '../package.json';
const Dashboard = lazy(() => import('./pages/Dashboard').then((module) => ({ default: module.Dashboard })));
const DataLogger = lazy(() => import('./pages/DataLogger').then(module => ({ default: module.DataLogger })));
const Measurements = lazy(() => import('./pages/Measurements').then(module => ({ default: module.Measurements })));
const Miners = lazy(() => import('./pages/Miners').then(module => ({ default: module.Miners })));
const Sockets = lazy(() => import('./pages/Sockets').then(module => ({ default: module.Sockets })));
const Settings = lazy(() => import('./pages/Settings').then(module => ({ default: module.Settings })));
const SystemPage = lazy(() => import('./pages/SystemPage').then(module => ({ default: module.SystemPage })));
const Placeholder = lazy(() => import('./pages/Placeholder').then((module) => ({ default: module.Placeholder })));

const navGroups = [
  { label: 'Übersicht', items: [
    { path: '/', label: 'Dashboard', Icon: IconGauge, description: 'Live-Daten und Steuerungsstatus' },
  ] },
  { label: 'Energie', items: [
    { path: '/messungen', label: 'Messungen', Icon: IconActivity, description: 'Quellen und Batteriesensoren' },
    { path: '/datalogger', label: 'Data Logger', Icon: IconChartBar, description: 'Historische Messwerte und Regler-Events' },
    { path: '/prognose', label: 'Prognose', Icon: IconCloud, description: 'Wetter- und PV-Prognosen', future: true },
  ] },
  { label: 'Mining', items: [
    { path: '/miner', label: 'Miner', Icon: IconCpu, description: 'Miner verwalten' },
    { path: '/sockets', label: 'Sockets', Icon: IconPlugConnected, description: 'Schaltbare Geräte' },
    { path: '/optimierung', label: 'Optimierung', Icon: IconWand, description: 'Leistungsprofile analysieren', future: true },
  ] },
  { label: 'System', items: [
    { path: '/einstellungen', label: 'Einstellungen', Icon: IconSettings, description: 'PV2Hash konfigurieren' },
    { path: '/system', label: 'System', Icon: IconTerminal2, description: 'Updates, Konsole und Diagnose' },
  ] },
];

function readInitialCollapsed(): boolean {
  try { return localStorage.getItem('pv2hash.ui-next.nav-collapsed') === 'true'; }
  catch { return false; }
}

export function App() {
  const [mobileOpened, { toggle: toggleMobile, close: closeMobile }] = useDisclosure(false);
  const [collapsed, setCollapsed] = useState(readInitialCollapsed);
  const location = useLocation();
  const { data: version } = useQuery({ queryKey: ['version'], queryFn: getVersion, refetchInterval: 60000 });
  const { isError: statusError } = useQuery({ queryKey: ['status'], queryFn: getStatus, refetchInterval: 15000 });
  const isMobile = useMediaQuery('(max-width: 48em)');
  const navCollapsed = collapsed && !isMobile;
  const updateAvailable=version?.update_available===true;
  const navbarWidth = navCollapsed ? 76 : 252;

  function toggleCollapsed() {
    setCollapsed((previous) => {
      const next = !previous;
      try { localStorage.setItem('pv2hash.ui-next.nav-collapsed', String(next)); } catch { /* optional */ }
      return next;
    });
  }

  return (
    <AppShell
      header={{ height: { base: 60, sm: 0 } }}
      navbar={{ width: navbarWidth, breakpoint: 'sm', collapsed: { mobile: !mobileOpened } }}
      padding={{ base: 'xs', md: 'md' }}
      className="pv-shell"
    >
      <AppShell.Header hiddenFrom="sm" className="pv-mobile-header">
        <Group h="100%" justify="space-between" px="md">
          <Group gap="sm">
            <Image src="/static/apple-touch-icon.png" w={32} h={32} alt="" />
            <Text fw={800}>PV2Hash</Text>
          </Group>
          <Box className="pv-mobile-nav-toggle">
            <Burger opened={mobileOpened} onClick={toggleMobile}
              aria-label={updateAvailable
                ? 'Navigation öffnen oder schließen – Update verfügbar'
                : 'Navigation öffnen oder schließen'} size="sm" />
            {updateAvailable && <span className="pv-update-dot pv-mobile-update-indicator"
              aria-hidden="true" />}
          </Box>
        </Group>
      </AppShell.Header>
      <AppShell.Navbar className="pv-navbar" p="xs">
        <AppShell.Section px={navCollapsed ? 4 : 'sm'} pt="sm" pb="md">
          <Group justify={navCollapsed ? 'center' : 'space-between'} align="center" wrap="nowrap">
            <Group gap={8} wrap="nowrap" style={{ minWidth: 0 }}>
              <Image src="/static/apple-touch-icon.png" w={38} h={38} alt="PV2Hash" />
              {!navCollapsed && (
                <Box>
                  <Text fw={900} size="lg" lh="1.2">PV2<span className="pv-brand-accent">Hash</span></Text>
                  <Text size="xs" c="dimmed">Mining-Energiemanager</Text>
                </Box>
              )}
            </Group>
          </Group>
          <Group justify="flex-end" visibleFrom="sm" mt="sm">
            <Tooltip label={navCollapsed ? 'Menü ausklappen' : 'Menü einklappen'}>
              <ActionIcon onClick={toggleCollapsed} variant="subtle" color="gray" aria-label="Desktop-Menü ein- oder ausklappen">
                {navCollapsed ? <IconChevronRight size={17} /> : <IconChevronLeft size={17} />}
              </ActionIcon>
            </Tooltip>
          </Group>
        </AppShell.Section>
        <Divider color="rgba(255,255,255,.08)" mb="xs" />
        <AppShell.Section grow component={ScrollArea} type="hover" scrollbarSize={4}>
          <Stack gap="sm">
            {navGroups.map((group) => (
              <Box key={group.label}>
                {!navCollapsed && <Text className="pv-nav-group" size="xs" c="dimmed" fw={700} px="sm" mb={5}>{group.label}</Text>}
                {group.items.map(({ path, label, Icon, future }) => (
                  <Tooltip key={path} label={label} disabled={!navCollapsed} position="right" withArrow>
                    <NavLink
                      component={Link}
                      to={path}
                      label={navCollapsed ? undefined : label}
                      title={navCollapsed ? label : undefined}
                      leftSection={<Icon size={20} stroke={1.8} />}
                      rightSection={!navCollapsed && path==='/system' && updateAvailable
                        ? <span className="pv-update-dot" title="Update verfügbar" />
                        : !navCollapsed && future ? <IconZoomCheck size={15} opacity={0.55} /> : undefined}
                      active={location.pathname === path}
                      variant="light"
                      className="pv-nav-item"
                      onClick={closeMobile}
                      aria-label={label}
                    />
                  </Tooltip>
                ))}
              </Box>
            ))}
          </Stack>
        </AppShell.Section>
        <AppShell.Section p={navCollapsed ? 'xs' : 'sm'}>
          <Divider color="rgba(255,255,255,.08)" mb="sm" />
          {!navCollapsed && <Stack gap={5}>
            <Text size="xs" c="dimmed" fw={650}>Versionen</Text>
            <Group gap="sm" justify="space-between" wrap="nowrap">
              <Text size="xs" c="dimmed">GUI <Text span c="inherit" fw={650}>{guiVersion}</Text></Text>
              <Text size="xs" c="dimmed">Core <Text span c="inherit" fw={650}>{version?.version_full || version?.version || '—'}</Text></Text>
            </Group>
            {updateAvailable && (
              <Text component={Link} to="/system" size="xs" c="orange" fw={650}
                className="pv-menu-update-link"
                onClick={closeMobile} aria-label="Update verfügbar – Systemseite öffnen">
                <span className="pv-update-dot" aria-hidden="true" />
                Update verfügbar
              </Text>
            )}
          </Stack>}
          {navCollapsed && updateAvailable && (
            <Tooltip label="Update verfügbar – Systemseite öffnen">
              <Box component={Link} to="/system" className="pv-menu-update-collapsed"
                aria-label="Update verfügbar – Systemseite öffnen">
                <span className="pv-update-dot" aria-hidden="true" />
              </Box>
            </Tooltip>
          )}
          <Group mt={navCollapsed ? 0 : 8} justify={navCollapsed ? 'center' : 'flex-start'} gap="xs">
            <Tooltip label={statusError ? 'Backend nicht erreichbar' : 'Backend verbunden'}>
              <Badge color={statusError ? 'red' : 'teal'} variant="light" size="sm" circle={navCollapsed}>
                {navCollapsed ? '●' : statusError ? 'Offline' : 'Verbunden'}
              </Badge>
            </Tooltip>
          </Group>
        </AppShell.Section>
      </AppShell.Navbar>
      <AppShell.Main className="pv-main">
        <Suspense fallback={<Center p="xl"><Loader color="violet" /></Center>}>
          <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/messungen" element={<Measurements />} />
          <Route path="/datalogger" element={<DataLogger />} />
          <Route path="/prognose" element={<Placeholder title="Prognose" description="Vorbereitung der Wetter- und PV-Ertragsprognosen." future />} />
          <Route path="/miner" element={<Miners />} />
          <Route path="/sockets" element={<Sockets />} />
          <Route path="/optimierung" element={<Placeholder title="Optimierung" description="Performance-Kennlinien und effiziente Minerprofile." future />} />
          <Route path="/einstellungen" element={<Settings />} />
          <Route path="/system" element={<SystemPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </AppShell.Main>
    </AppShell>
  );
}
