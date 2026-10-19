<script lang="ts">
  import { logout } from '../lib/store';
  import AccountsSection from './settings/AccountsSection.svelte';
  import CategoriesSection from './settings/CategoriesSection.svelte';
  import DevicesSection from './settings/DevicesSection.svelte';
  import InstitutionsSection from './settings/InstitutionsSection.svelte';
  import PasswordSection from './settings/PasswordSection.svelte';
  import RulesSection from './settings/RulesSection.svelte';
  import SecuritySection from './settings/SecuritySection.svelte';
</script>

<InstitutionsSection />
<AccountsSection />
<CategoriesSection />
<RulesSection />
<DevicesSection />
<SecuritySection />
<PasswordSection />
<section class="card">
  <button type="button" onclick={() => logout()}>Log out</button>
</section>
