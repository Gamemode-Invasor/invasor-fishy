import { currentGame, defineModule, ui, type FormStore, type ModuleCtx, type SettingsForm, type SettingValue } from "invasor";

// Pescao: MAKO Renderer (frame generation, scaling) from the panel.
// Game: one switch per game. On = the game gets its own profile (named after it, its id
// in active_in), all its options show below, and the launch option MAKO needs is there
// to copy. Off = the game is taken out of it.
// Manage: MAKO's global options and every profile. Everything lives in MAKO's own
// conf.toml; Pescao stores nothing of its own.

interface Status {
  installed: boolean;
  path: string;
  exists: boolean;
  error: string | null;
  /** MAKO's own validator complains about the current conf.toml. */
  mako_problem: string | null;
}

interface ProfileInfo {
  name: string;
  games: { entry: string; name: string | null }[];
}

const errorText = (e: unknown) => String((e as Error)?.message ?? e);

// Forms on screen. Some options change others, as in mako-ui (e.g. Ultra performance sets
// flow scale, the lighter model and FP16; keep in sync with mako.change): after saving one
// of those, every form shows what's stored. Other saves don't reload (a held slider would jump).
const forms = new Set<SettingsForm>();
const CHANGES_OTHERS = new Set([
  "ultra_performance",
  "dynamic_cadence_recovery",
  "base_fps_cap",
  "adaptive_auto_base_fps_cap",
  "adaptive_fractional_real_frame_priority",
]);

function refreshForms() {
  for (const f of forms) {
    if (f.isConnected) void f.reload().catch(() => {});
    else forms.delete(f);
  }
}

/** A FormStore backed by two backend methods. */
function store(ctx: ModuleCtx, get: string, set: string, extra: Record<string, unknown> = {}): FormStore {
  return {
    get: () => ctx.call<Record<string, SettingValue>>(get, extra),
    set: async <T extends SettingValue>(key: string, value: T) => {
      const stored = await ctx.call<T>(set, { ...extra, key, value });
      if (CHANGES_OTHERS.has(key)) refreshForms();
      return stored;
    },
  };
}

/** ui.form, kept up to date by refreshForms(). */
async function form(ctx: ModuleCtx, name: string, st: FormStore): Promise<SettingsForm> {
  const f = await ui.form(ctx, name, st, { navHints: false });
  forms.add(f);
  return f;
}

const TESTED = "4.0"; // keep in sync with updates.TESTED

/** MAKO's shaders (its bundled vkBasalt) are left out of Pescao for now. */
const SHADERS_NOTE = "Shaders (MAKO's vkBasalt effects) aren't set up by Pescao for now: use mako-ui for them, and add the variables it shows to the launch option.";

/** Copies text to the clipboard: the Clipboard API, else the older copy command. */
async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    /* not allowed here: try the old way */
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0;";
  document.body.append(area);
  try {
    area.select();
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    area.remove();
  }
}

/**
 * The launch option for a profile (as mako-ui builds it) with a Copy button. MAKO uses the
 * profile named by MAKO_PROFILE in it: MAKO 4.0 doesn't match games by their Steam id.
 */
async function launchOption(ctx: ModuleCtx, name: string): Promise<HTMLElement[]> {
  let opt: { line: string; found: boolean };
  try {
    opt = await ctx.call("launch_option", { name });
  } catch (e) {
    return [ui.info(errorText(e))];
  }
  const items: HTMLElement[] = [
    ui.info(opt.line),
    ui.button({
      label: "Copy launch option",
      onClick: async () => {
        if (await copyText(opt.line)) ctx.toast("Copied: paste it in the game's launch options");
        else ctx.toast("Couldn't copy here: type it in as shown", "error");
      },
    }),
    ui.info("Paste it in the game's Properties › General › Launch options; keep your own variables before mako-launch. MAKO only runs, with this profile, in games started this way."),
  ];
  if (!opt.found) items.push(ui.info("⚠ mako-launch isn't there: install MAKO Renderer (Updates tab)."));
  return items;
}

/** A note when the installed MAKO isn't the series Pescao was tested with. */
async function compatNote(ctx: ModuleCtx): Promise<HTMLElement[]> {
  try {
    const c = await ctx.call<{ installed: string | null; compat: string | null; tested: string }>("compat");
    if (c.compat === "newer_minor" || c.compat === "unsupported")
      return [ui.info(`⚠ MAKO ${c.installed} is newer than what Pescao was tested with (${c.tested}): some options may be missing.`)];
    if (c.compat === "older") return [ui.info(`⚠ MAKO ${c.installed} is older than what Pescao was tested with (${c.tested}).`)];
  } catch {
    /* not essential */
  }
  return [];
}

// ---------- Game ----------

let gameEl: HTMLElement | null = null;
let gameSig = "";

async function renderGame(ctx: ModuleCtx, force = false) {
  const el = gameEl;
  if (!el) return;
  const t = currentGame(ctx.game());
  let status: Status | null = null;
  let profiles: ProfileInfo[] = [];
  let current: { entry: string; profile: string | null; custom: boolean } | null = null;
  let problem: string | null = null;
  try {
    status = await ctx.call<Status>("status");
    if (!status.error) {
      profiles = await ctx.call<ProfileInfo[]>("profiles");
      if (t) current = await ctx.call("game_profile", { appid: t.game.appid, shortcut: t.game.shortcut });
    }
  } catch (e) {
    problem = errorText(e);
  }
  const sig = JSON.stringify([t?.game.appid, t?.how, status, profiles.map((p) => p.name), current, problem]);
  if (!force && sig === gameSig) return; // nothing changed: keep the ring where it is
  gameSig = sig;

  const parts: HTMLElement[] = await compatNote(ctx);
  if (status && !status.installed) parts.push(ui.info("MAKO Renderer doesn't seem to be installed (no Vulkan layer found)."));
  if (status?.error) parts.push(ui.info(status.error));
  if (!t) {
    parts.push(ui.info("Open or highlight a game in the Library."));
  } else {
    parts.push(ui.info(`${t.game.name ?? `App ${t.game.appid}`} (${t.how})`));
    if (problem) parts.push(ui.info(problem));
    else if (status?.error) {
      /* explained above */
    } else if (current) {
      const game = t.game;
      const cur = current;
      const args = { appid: game.appid, shortcut: game.shortcut };
      parts.push(
        ui.toggle({
          label: "MAKO Renderer",
          value: cur.profile !== null,
          hint: "Gives this game its own MAKO profile. MAKO runs once the game starts through the launch option below.",
          navHints: false,
          onChange: async (on) => {
            try {
              if (on) {
                const made = await ctx.call<{ profile: string }>("make_custom", { ...args, title: game.name });
                ctx.toast(`On: profile “${made.profile}”`);
              } else {
                await ctx.call("set_game_profile", { ...args, name: null });
                ctx.toast("Off for this game");
              }
            } catch (e) {
              ctx.toast(`Couldn't change it: ${errorText(e)}`, "error");
            }
            void renderGame(ctx, true); // show or hide the options
          },
        }),
      );
      // Right under the switch, only while it's on: what the game needs to use MAKO.
      if (cur.profile) parts.push(...(await launchOption(ctx, cur.profile)));
      if (cur.profile && cur.custom) {
        parts.push(await form(ctx, "profile", store(ctx, "profile_get", "profile_set", { name: cur.profile })), ui.info(`ⓘ ${SHADERS_NOTE}`));
      } else if (cur.profile) {
        // Set up elsewhere (e.g. mako-ui) with a profile other games use too.
        const n = profiles.find((p) => p.name === cur.profile)?.games.length ?? 0;
        parts.push(
          ui.info(`Uses the shared profile “${cur.profile}”${n > 1 ? ` (${n} games)` : ""}.`),
          ui.button({
            label: "Give this game its own profile",
            onClick: async () => {
              try {
                const made = await ctx.call<{ profile: string }>("make_custom", { ...args, title: game.name });
                ctx.toast(`Profile “${made.profile}” for this game`);
              } catch (e) {
                ctx.toast(errorText(e), "error");
              }
              void renderGame(ctx, true);
            },
          }),
        );
      }
      if (cur.profile)
        parts.push(ui.info(`Its id (${cur.entry}${game.shortcut ? ", non-Steam" : ""}) is kept in the profile's active_in to tell which game uses it; MAKO picks the profile from MAKO_PROFILE in the launch option.`));
    }
  }
  el.replaceChildren(...parts);
}

// ---------- Profiles ----------

let profilesEl: HTMLElement | null = null;
let selected: string | null = null;

async function renderProfiles(ctx: ModuleCtx, pick?: string) {
  const el = profilesEl;
  if (!el) return;
  let list: ProfileInfo[];
  try {
    list = await ctx.call<ProfileInfo[]>("profiles");
  } catch (e) {
    el.replaceChildren(ui.info(errorText(e)));
    return;
  }
  const names = list.map((p) => p.name);
  selected = pick !== undefined && names.includes(pick) ? pick : selected && names.includes(selected) ? selected : (names[0] ?? null);

  const run = (label: string, fn: () => Promise<void>) =>
    ui.button({
      label,
      onClick: async () => {
        try {
          await fn();
        } catch (e) {
          ctx.toast(errorText(e), "error");
        }
      },
    });
  const newName = ui.text({ label: "Name", value: "", maxLength: 64, placeholder: "for create / duplicate / rename" });
  const typed = () => newName.get().trim();
  const manage: HTMLElement[] = [
    newName,
    run("Create", async () => {
      await ctx.call("profile_create", { name: typed() });
      ctx.toast(`Profile “${typed()}” created`);
      await renderProfiles(ctx, typed());
    }),
  ];

  const parts: HTMLElement[] = [];
  if (selected) {
    const name = selected;
    const profile = list.find((p) => p.name === name)!;
    parts.push(
      ui.select({ label: "Profile", value: name, options: names.map((n) => ({ value: n, label: n })), onChange: (v) => void renderProfiles(ctx, v) }),
      await form(ctx, "profile", store(ctx, "profile_get", "profile_set", { name })),
      ui.info(`ⓘ ${SHADERS_NOTE}`),
      ui.section("Launch option", await launchOption(ctx, name), { open: false }),
      ui.section(
        "Games using it",
        profile.games.length
          ? profile.games.map((g) => ui.info(g.name ? `${g.name} (${g.entry})` : g.entry))
          : [ui.info("No games use it.")],
        { open: false },
      ),
    );
    manage.push(
      run("Duplicate this profile", async () => {
        await ctx.call("profile_create", { name: typed(), copy_from: name });
        ctx.toast(`“${name}” duplicated as “${typed()}”`);
        await renderProfiles(ctx, typed());
      }),
      run("Rename this profile", async () => {
        await ctx.call("profile_rename", { old: name, new: typed() });
        ctx.toast(`Renamed to “${typed()}”`);
        await renderProfiles(ctx, typed());
      }),
      ui.info("The launch option names the profile (MAKO_PROFILE): after a rename, copy it again for the games that use it."),
      run("Delete this profile", async () => {
        const games = profile.games.length ? ` Its ${profile.games.length} game(s) will go back to no MAKO profile.` : "";
        if (!(await ui.confirm(`Delete the profile “${name}”?${games}`, { ok: "Delete" }))) return;
        await ctx.call("profile_delete", { name });
        ctx.toast(`Profile “${name}” deleted`);
        await renderProfiles(ctx);
      }),
    );
  } else {
    parts.push(ui.info("No profiles yet: type a name below and create one."));
  }
  parts.push(ui.section("Create, duplicate, rename, delete", manage, { open: !selected }));
  el.replaceChildren(...parts);
  gameSig = ""; // the Game tab's list of profiles may have changed
}

// ---------- module ----------

let skipShow = false; // a tab's first onShow comes right after its render

export default defineModule({
  tabs: [
    {
      label: "Game",
      async render(el, ctx) {
        gameEl = el;
        await renderGame(ctx, true);
      },
      onShow: (ctx) => void renderGame(ctx),
    },
    {
      label: "Manage",
      async render(el, ctx) {
        const globalEl = document.createElement("div");
        profilesEl = document.createElement("div");
        el.append(...(await compatNote(ctx)), profilesEl, globalEl);
        skipShow = true;
        await renderProfiles(ctx);
        try {
          const status = await ctx.call<Status>("status");
          const body: HTMLElement[] = [ui.info(`Config: ${status.path}`)];
          if (status.mako_problem) el.prepend(ui.info(`⚠ MAKO reports a problem in conf.toml: ${status.mako_problem}`));
          if (status.error) body.push(ui.info(status.error));
          else body.push(await form(ctx, "global", store(ctx, "global_get", "global_set")));
          globalEl.append(ui.section("Global options", body, { open: false }));
          let launcherBody: HTMLElement[];
          try {
            launcherBody = [await form(ctx, "launcher", store(ctx, "launcher_get", "launcher_set"))];
          } catch (e) {
            launcherBody = [ui.info(errorText(e))];
          }
          globalEl.append(ui.section("Launcher (all games)", launcherBody, { open: false }));
        } catch (e) {
          globalEl.append(ui.info(errorText(e)));
        }
      },
      // Another tool (mako-ui) may have changed the file meanwhile.
      onShow: (ctx) => {
        if (skipShow) skipShow = false;
        else void renderProfiles(ctx);
      },
    },
    {
      label: "Updates",
      render(el, ctx) {
        const result = document.createElement("div");
        const check = async () => {
          result.replaceChildren(ui.info("Checking GitHub…"));
          let st: {
            installed: string | null;
            latest: string;
            state: string;
            path: string | null;
            tested: string;
            latest_compat: string | null;
          };
          try {
            st = await ctx.call("update_status");
          } catch (e) {
            result.replaceChildren(ui.info(`Couldn't check: ${errorText(e)}`));
            return;
          }
          const lines: HTMLElement[] = [
            ui.info(`Installed: ${st.installed ?? (st.state === "not_installed" ? "not installed" : "unknown version")}`),
            ui.info(`Latest stable: ${st.latest}`),
          ];
          if (st.state === "up_to_date") lines.push(ui.info("You're up to date."));
          else if (st.state === "ahead") lines.push(ui.info(`Your version is newer than the latest stable (${st.latest}).`));
          else if (st.state === "system")
            lines.push(ui.info(`MAKO Renderer is installed by your system (${st.path}), not in ~/.local: update it with your package manager.`));
          else if (st.state === "decky") lines.push(ui.info("MAKO Renderer is managed by MAKO Decky: update it from there."));
          else if (st.latest_compat === "unsupported")
            lines.push(ui.info(`MAKO Renderer ${st.latest} is a new major version that this Pescao doesn't know: update Pescao first.`));
          else
            lines.push(
              ui.button({
                label: `Install ${st.latest}`,
                onClick: async () => {
                  let what = st.installed
                    ? `Update MAKO Renderer from ${st.installed} to ${st.latest}? Your profiles are kept.`
                    : `Install MAKO Renderer ${st.latest} in ~/.local?`;
                  if (st.latest_compat === "newer_minor")
                    what += ` Pescao was tested with MAKO ${st.tested}: new options won't show here (set them with mako-ui).`;
                  if (!(await ui.confirm(what, { ok: "Install" }))) return;
                  ctx.toast(`Installing MAKO Renderer ${st.latest}…`);
                  try {
                    await ctx.call("update_install");
                    ctx.toast(`MAKO Renderer ${st.latest} installed`);
                  } catch (e) {
                    ctx.toast(`Not installed: ${errorText(e)}`, "error");
                  }
                  void check();
                },
              }),
              ui.info("Games use the new version the next time they start."),
            );
          result.replaceChildren(...lines);
        };
        el.append(
          ui.info(`This Pescao is tested with MAKO Renderer ${TESTED}.`),
          ui.info("Stable MAKO Renderer releases from GitHub, installed into ~/.local by MAKO's own installer."),
          ui.button({ label: "Check for updates", onClick: () => void check() }),
          result,
        );
      },
    },
    {
      label: "Credits",
      render(el) {
        el.append(
          ui.info("Pescao is a panel for MAKO Renderer: frame generation, scaling and shaders on Linux (github.com/eugeniosegala/MAKO)."),
          ui.separator(),
          ui.info("Thanks to Eugenio Segala and every MAKO contributor."),
          ui.info("Thanks to PancakeTAS and the lsfg-vk contributors, the project MAKO comes from."),
          ui.info("Thanks to THS, the developer of Lossless Scaling, whose frame generation MAKO uses (you need your own copy of Lossless Scaling)."),
          ui.separator(),
          ui.info("Pescao is not affiliated with MAKO, lsfg-vk or Lossless Scaling. Please don't report problems with this module to them."),
        );
      },
    },
  ],
  tabsAlign: "justify",
  onGameChange: (_game, ctx) => void renderGame(ctx),
  destroy: () => {
    gameEl = profilesEl = null;
    forms.clear();
    gameSig = "";
    selected = null;
  },
});
