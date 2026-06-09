import { createApp } from '@backstage/frontend-defaults';
import catalogPlugin from '@backstage/plugin-catalog/alpha';
import scaffolderPlugin from '@backstage/plugin-scaffolder/alpha';
import searchPlugin from '@backstage/plugin-search/alpha';
import userSettingsPlugin from '@backstage/plugin-user-settings/alpha';
import catalogImportPlugin from '@backstage/plugin-catalog-import/alpha';
import orgPlugin from '@backstage/plugin-org/alpha';
import kubernetesPlugin from '@backstage/plugin-kubernetes/alpha';
import { crossplaneResourcesPlugin } from '@terasky/backstage-plugin-crossplane-resources-frontend/alpha';
import { navModule } from './modules/nav';

export default createApp({
  features: [
    catalogPlugin,
    scaffolderPlugin,
    searchPlugin,
    userSettingsPlugin,
    catalogImportPlugin,
    orgPlugin,
    kubernetesPlugin,
    crossplaneResourcesPlugin,
    navModule,
  ],
});
