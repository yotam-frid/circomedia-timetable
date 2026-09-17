<script>
  import { ArrowLeft, ArrowRight, ExternalLink } from "@lucide/svelte";
  import {
    prettySubject,
    parseICS,
    formatDayLabel,
    initialDayIndex,
  } from "$lib/api.js";

  /** Renders a matched student + their schedule. */
  let { student, updated } = $props();

  // DEBUG: fixed clock for UI testing (Thu 17 Sep, 10:45).
  // Revert to the real clock when done: const now = new Date();
  const now = new Date(2026, 8, 17, 10, 45);
  const nowKey = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  const nowMinutes = now.getHours() * 60 + now.getMinutes();

  let groupsOpen = $state(false);

  let calState = $state("loading"); // loading | ready | empty | error
  let days = $state([]);
  let dayIndex = $state(0);
  let current = $derived(days[dayIndex]);
  let visibleEvents = $derived(current ? withGaps(current.events) : []);
  let isToday = $derived(current?.key === nowKey);
  // Index of the next class (the single NEXT entry): first non-break
  // event starting after now. Skips free-time gaps so the label always
  // lands on the upcoming class, including the first entry of the day.
  let nextIdx = $derived(
    isToday
      ? visibleEvents.findIndex((ev) => {
          if (ev.free) return false;
          const s = minutesOf(ev.start);
          return s != null && s > nowMinutes;
        })
      : -1,
  );

  function isNow(ev) {
    if (!isToday) return false;
    const s = minutesOf(ev.start);
    const e = minutesOf(ev.end);
    return s != null && e != null && s <= nowMinutes && nowMinutes < e;
  }

  function minsUntil(ev) {
    const s = minutesOf(ev.start);
    return s == null ? null : s - nowMinutes;
  }

  // Refetch the student's .ics feed whenever a different student is shown.
  $effect(() => {
    const url = student.feed.https;
    groupsOpen = false;
    calState = "loading";
    days = [];
    dayIndex = 0;
    let cancelled = false;
    fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error("feed fetch failed");
        return res.text();
      })
      .then((text) => {
        if (cancelled) return;
        const parsed = parseICS(text);
        days = parsed;
        if (!parsed.length) {
          calState = "empty";
        } else {
          dayIndex = initialDayIndex(parsed);
          calState = "ready";
        }
      })
      .catch(() => {
        if (!cancelled) calState = "error";
      });
    return () => {
      cancelled = true;
    };
  });

  function minutesOf(label) {
    const m = /^(\d+):(\d+)$/.exec(label ?? "");
    return m ? +m[1] * 60 + +m[2] : null;
  }

  function withGaps(events) {
    const out = [];
    for (let i = 0; i < events.length; i++) {
      const ev = events[i];
      if (i > 0) {
        const prev = events[i - 1];
        const prevEnd = minutesOf(prev.end);
        const start = minutesOf(ev.start);
        if (prevEnd != null && start != null && start - prevEnd >= 20) {
          out.push({
            start: prev.end,
            end: ev.start,
            title: "Free time",
            location: "",
            free: true,
          });
        }
      }
      out.push(ev);
    }
    return out;
  }

  function durationLabel(start, end) {
    const s = minutesOf(start);
    const e = minutesOf(end);
    if (s == null || e == null || e <= s) return "break";
    const mins = e - s;
    if (mins < 60) return `${mins}min break`;
    const h = Math.floor(mins / 60);
    const rest = mins % 60;
    return rest === 0
      ? `${h}hr break`
      : `${h}.${String(rest).padStart(2, "0")}hr break`;
  }

  function prevDay() {
    if (dayIndex > 0) dayIndex -= 1;
  }

  function nextDay() {
    if (dayIndex < days.length - 1) dayIndex += 1;
  }
</script>

<article
  class="overflow-hidden rounded-2xl border border-line bg-white shadow-sm"
>
  <button
    type="button"
    onclick={() => (groupsOpen = !groupsOpen)}
    aria-expanded={groupsOpen}
    aria-controls="groups-section"
    class="flex w-full items-center justify-between gap-3 border-b border-line px-6 py-4 text-left"
  >
    <span class="font-serif text-2xl text-ink-900">{student.name}</span>
    <span class="flex shrink-0 items-center gap-2">
      <span
        class="rounded-full border border-line bg-cream-100 px-3 py-1 text-sm font-medium text-ink-500"
      >
        Year {student.year}
      </span>
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
        class="h-5 w-5 text-ink-400 transition-transform duration-200"
        class:rotate-180={groupsOpen}
      >
        <path d="m6 9 6 6 6-6" />
      </svg>
    </span>
  </button>

  <div
    id="groups-section"
    class="grid transition-[grid-template-rows] duration-300 ease-in-out"
    class:grid-rows-[0fr]={!groupsOpen}
    class:grid-rows-[1fr]={groupsOpen}
  >
    <div
      class="min-h-0 overflow-hidden transition-opacity duration-300"
      class:opacity-0={!groupsOpen}
      class:opacity-100={groupsOpen}
    >
      <div class="border-b border-line px-6 py-2">
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
          <p class="py-1.5 text-ink-500">No group info this week</p>
        {/if}
      </div>
    </div>
  </div>

  <section aria-label="Class schedule">
    <div class="flex items-center justify-center py-2">
      <div class="flex items-center gap-1">
        <button
          type="button"
          onclick={prevDay}
          disabled={calState !== "ready" || dayIndex <= 0}
          aria-label="Previous day"
          class="cursor-pointer rounded-lg px-3 py-1.5 text-xl leading-none transition disabled:cursor-default disabled:text-ink-300 disabled:opacity-40 not-disabled:text-ink-800 not-disabled:hover:bg-cream-100 not-disabled:hover:text-coral-600"
        >
          <ArrowLeft class="h-5 w-5" />
        </button>
        <span
          class="inline-block w-24 whitespace-nowrap text-center text-base font-medium text-ink-800"
        >
          {#if current}
            {formatDayLabel(current.key)}
          {:else}
            &nbsp;
          {/if}
        </span>
        <button
          type="button"
          onclick={nextDay}
          disabled={calState !== "ready" || dayIndex >= days.length - 1}
          aria-label="Next day"
          class="cursor-pointer rounded-lg px-3 py-1.5 text-xl leading-none transition disabled:cursor-default disabled:text-ink-300 disabled:opacity-40 not-disabled:text-ink-800 not-disabled:hover:bg-cream-100 not-disabled:hover:text-coral-600"
        >
          <ArrowRight class="h-5 w-5" />
        </button>
      </div>
    </div>

    <div class="px-6 pb-2">
      {#if calState === "loading"}
        <p class="py-1.5 text-ink-500">Loading schedule…</p>
      {:else if calState === "error"}
        <p class="py-1.5 text-ink-500">
          Couldn't load the schedule — try again in a minute.
        </p>
      {:else if calState === "empty"}
        <p class="py-1.5 text-ink-500">No scheduled classes</p>
      {:else if current}
        <ul class="divide-y divide-cream-100">
          {#each visibleEvents as ev, idx (`${ev.start}-${ev.end}-${ev.title}-${ev.location}-${ev.free}`)}
            {@const live = isNow(ev)}
            {@const mins = idx === nextIdx ? minsUntil(ev) : null}
            <li
              class={live
                ? "-mx-2 rounded-lg bg-coral-100/60 px-2 py-2.5"
                : "py-2.5"}
            >
              {#if ev.free}
                <div class="text-xs text-ink-400">
                  {ev.start}-{ev.end}
                  <span class="text-ink-500"
                    >{durationLabel(ev.start, ev.end)}</span
                  >
                  {#if live}
                    <span class="font-semibold text-coral-600">now</span>
                  {/if}
                </div>
              {:else}
                <div class="text-xs text-ink-400">
                  {ev.start}-{ev.end}
                  {#if live}
                    <span class="font-semibold text-coral-600">now</span>
                  {:else if mins != null && mins < 60}
                    <span class="font-semibold text-coral-600"
                      >in {mins}min</span
                    >
                  {/if}
                </div>
                <div class="font-bold text-ink-900">{ev.title}</div>
                {#if ev.location}
                  <div class="text-ink-500">{ev.location}</div>
                {/if}
              {/if}
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  </section>

  <div class="border-t border-line px-6 py-4">
    <div class="flex items-center justify-center gap-3 pb-1">
      <span class="text-sm text-ink-400">Add to calendar: </span>
      <div class="flex items-center gap-3">
        <a
          href={student.feed.webcal}
          class="inline-flex items-center gap-1 text-sm font-medium text-ink-400 underline decoration-dashed underline-offset-2 transition hover:text-ink-600"
          ><ExternalLink class="h-3.5 w-3.5" />Apple
        </a>
        <a
          href={student.feed.google}
          target="_blank"
          rel="noopener"
          class="inline-flex items-center gap-1 text-sm font-medium text-ink-400 underline decoration-dashed underline-offset-2 transition hover:text-ink-600"
          ><ExternalLink class="h-3.5 w-3.5" />Google
        </a>
      </div>
    </div>
    <p class="text-center text-sm text-ink-400">
      Updated <span class="text-ink-500">{updated}</span>
    </p>
  </div>
</article>
