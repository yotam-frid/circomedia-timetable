<script>
  import { prettySubject, copyText } from "$lib/api.js";

  /** Renders a matched student + their calendar feed links. */
  let { student } = $props();

  let copied = $state(false);

  async function copy() {
    if (await copyText(student.feed.https)) {
      copied = true;
      setTimeout(() => (copied = false), 1500);
    }
  }
</script>

<article
  class="overflow-hidden rounded-2xl border border-line bg-white shadow-sm"
>
  <header
    class="flex items-center justify-between border-b border-line px-6 py-4"
  >
    <h2 class="font-serif text-2xl text-ink-900">{student.name}</h2>
    <span
      class="rounded-full border border-line bg-cream-100 px-3 py-1 text-sm font-medium text-ink-500"
    >
      Year {student.year}
    </span>
  </header>

  <div class="px-6 py-2">
    {#if Object.keys(student.groups ?? {}).length}
      <ul class="divide-y divide-cream-100">
        {#each Object.entries(student.groups) as [subject, group] (subject)}
          <li class="flex items-baseline justify-between py-1.5">
            <span class="font-medium text-ink-800"
              >{prettySubject(subject)}</span
            >
            <span class="text-ink-500">{group}</span>
          </li>
        {/each}
      </ul>
    {:else}
      <p class="text-ink-500">No group info this week</p>
    {/if}
  </div>

  <div class="flex flex-col gap-2 border-t border-line px-6 py-5">
    <label
      for="feed-url"
      class="block text-sm font-medium uppercase tracking-wider text-ink-400"
    >
      Get personal calendar
    </label>
    <a
      href={student.feed.webcal}
      class="relative flex flex-1 items-center justify-center rounded-xl border border-line bg-white px-4 py-3 text-center text-sm font-medium text-ink-800 transition hover:border-line-dark hover:bg-cream-50"
    >
      <img src="/apple-logo.svg" alt="" class="absolute left-4 h-4 w-auto" />
      Add to Apple Calendar
    </a>
    <a
      href={student.feed.google}
      target="_blank"
      rel="noopener"
      class="relative flex flex-1 items-center justify-center rounded-xl border border-line bg-white px-4 py-3 text-center text-sm font-medium text-ink-800 transition hover:border-line-dark hover:bg-cream-50"
    >
      <img src="/google-logo.png" alt="" class="absolute left-4 h-4 w-auto" />
      Add to Google Calendar
    </a>
  </div>

  <div class="border-t border-line px-6 py-5">
    <label
      for="feed-url"
      class="block text-sm font-medium uppercase tracking-wider text-ink-400"
    >
      Private calendar feed
    </label>
    <div class="mt-2 flex gap-2">
      <input
        id="feed-url"
        readonly
        value={student.feed.https}
        onfocus={(e) => e.currentTarget.select()}
        class="w-full rounded-xl border border-line bg-cream-50 px-4 py-2.5 font-mono text-sm text-ink-500 outline-none focus:border-coral-500"
      />
      <button
        onclick={copy}
        class="shrink-0 rounded-xl bg-ink-900 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-ink-800 focus:outline-none focus-visible:ring-4 focus-visible:ring-coral-500/30"
      >
        {copied ? "Copied!" : "Copy"}
      </button>
    </div>
  </div>
</article>
