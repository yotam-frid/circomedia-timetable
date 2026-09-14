<script>
  import { searchStudent, pickStudent } from "$lib/api.js";

  /** Search box. Emits `result` with the /api/student payload. */
  let { onresult = () => {} } = $props();

  let query = $state("");
  let searching = $state(false);
  let timer;

  async function run(name, exact = false) {
    searching = true;
    try {
      onresult(
        name ? await searchStudent(name, { exact }) : { status: "empty" },
        name,
      );
    } catch {
      onresult({ status: "error" }, name);
    } finally {
      searching = false;
    }
  }

  function oninput() {
    clearTimeout(timer);
    timer = setTimeout(() => run(query.trim()), 300);
  }

  const searchIcon = "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm10 2-4.35-4.35";
</script>

<div class="relative">
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.75"
    stroke-linecap="round"
    stroke-linejoin="round"
    class="pointer-events-none absolute left-5 top-1/2 h-5 w-5 -translate-y-1/2 text-ink-300"
    aria-hidden="true"
  >
    <path d={searchIcon} />
  </svg>
  <input
    type="search"
    autocomplete="off"
    bind:value={query}
    {oninput}
    class="w-full rounded-2xl border border-line bg-white py-4 pl-13 pr-12 font-serif text-xl text-ink-900 shadow-sm outline-none transition placeholder:font-sans placeholder:text-lg placeholder:text-ink-300 hover:border-line-dark focus:border-coral-500 focus:ring-4 focus:ring-coral-500/15"
  />
  {#if searching}
    <span
      role="status"
      class="absolute right-6 top-1/2 -translate-y-1/2 text-sm font-medium text-coral-600"
    >
      Searching…
    </span>
  {/if}
</div>
