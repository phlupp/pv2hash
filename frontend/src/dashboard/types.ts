import type { ComponentType } from 'react';
import type { DashboardLiveResponse, StatusResponse } from '../api';

export type DashboardContext = {
  live: DashboardLiveResponse | undefined;
  telemetry: StatusResponse | undefined;
  loading: boolean;
};

/**
 * Feature modules export a descriptor. The dashboard renders registered
 * sections by order and evaluates eligibility against current capabilities.
 * Disabled/not-yet-implemented features are never shown as live panels.
 */
export type DashboardSectionDefinition = {
  id: string;
  order: number;
  Component: ComponentType<{ context: DashboardContext }>;
  enabled?: (context: DashboardContext) => boolean;
};
