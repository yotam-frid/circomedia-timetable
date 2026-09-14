<script>
  /** Ambiguous name: let the user pick exactly who they are. */
  let { matches, onpick = () => {} } = $props();
</script>

<div class="rounded-2xl border border-line bg-white p-6 shadow-sm">
  <p class="font-serif text-2xl text-ink-900">A few people match — who are you?</p>
  <ul class="mt-4 divide-y divide-cream-100">
    {#each matches as m (m.slug)}
      <li>
        <button
          onclick={() => onpick(m.name)}
          class="group flex w-full items-center justify-between rounded-lg px-3 py-3 text-left transition hover:bg-cream-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-coral-500/30"
        >
          <span class="text-lg font-medium text-ink-800 group-hover:text-coral-600">{m.name}</span>
          <span class="rounded-full border border-line bg-cream-100 px-2.5 py-1 text-xs font-medium text-ink-400">
            Year {m.year}
          </span>
        </button>
      </li>
    {/each}
  </ul>
</div>