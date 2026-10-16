import type { DashboardSectionDefinition } from './types';

/**
 * Feature discovery at build time:
 * each module in sections/ exports "dashboardSection" (descriptor).
 * Adding/removing a feature dashboard panel needs no modification here.
 *
 * Visibility must reflect actual backend capabilities/feature enablement,
 * not an invented demo flag. Optional features use descriptor.enabled(ctx).
 */
const featureModules = import.meta.glob<{ dashboardSection: DashboardSectionDefinition }>(
  './sections/*.tsx',
  { eager: true },
);

export const dashboardSections: readonly DashboardSectionDefinition[] = Object.entries(featureModules)
  .map(([path, module]) => {
    if (!module.dashboardSection || !module.dashboardSection.id || !module.dashboardSection.Component) {
      throw new Error(`Dashboard-Modul ${path} muss dashboardSection exportieren.`);
    }
    return module.dashboardSection;
  })
  .sort((a, b) => a.order - b.order);
