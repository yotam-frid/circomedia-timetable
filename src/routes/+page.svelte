<script>
  import StudentSearch from "$lib/StudentSearch.svelte";
  import StudentCard from "$lib/StudentCard.svelte";
  import StudentPicker from "$lib/StudentPicker.svelte";
  import { fetchMeta, formatUpdated, pickStudent } from "$lib/api.js";
  import { onMount } from "svelte";

  let result = $state({ status: "empty" });
  let updated = $state("…");

  function onresult(r) {
    result = r;
  }

  async function onpick(name) {
    result = await pickStudent(name).catch(() => ({ status: "error" }));
  }

  onMount(async () => {
    const meta = await fetchMeta().catch(() => null);
    updated = formatUpdated(meta?.updated_at);
  });
</script>

<main class="mx-auto flex min-h-screen max-w-md flex-col px-6">
  <nav
    class="flex items-center pt-6"
    class:flex-1={result.status === "empty"}
    class:justify-between={result.status !== "empty"}
    class:justify-center={result.status === "empty"}
  >
    <span
      class="font-serif tracking-tight text-ink-900"
      class:text-xl={result.status !== "empty"}
      class:text-4xl={result.status === "empty"}>Circomedia Timetable</span
    >
  </nav>

  <section class="flex flex-1 flex-col mt-1">
    {#if result.status === "empty"}
      <span class="max-w-sm mb-4">Enter your <b>first name</b>.</span>
    {/if}
    <StudentSearch {onresult} />

    {#if result.status !== "empty"}
      <div aria-live="polite" class="mt-2">
        {#if result.status === "match"}
          <StudentCard student={result.student} {updated} />
        {:else if result.status === "picker"}
          <StudentPicker matches={result.matches} {onpick} />
        {:else if result.status === "none"}
          <div class="rounded-2xl border border-line bg-white p-6 text-center">
            <p class="text-ink-500">Couldn't find “{result.query}”.</p>
            <p class="mt-1 text-sm text-ink-400">Check the spelling.</p>
          </div>
        {:else if result.status === "too-many"}
          <div class="rounded-2xl border border-line bg-white p-6 text-center">
            <p class="text-ink-500">
              {result.count} people match “{result.query}” — type a bit more of
              the name.
            </p>
          </div>
        {:else if result.status === "error"}
          <div class="rounded-2xl border border-line bg-white p-6 text-center">
            <p class="text-ink-500">
              Timetable is updating — try again in a minute.
            </p>
          </div>
        {/if}
      </div>
    {/if}
  </section>
</main>
