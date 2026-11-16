import type { EngineInterface, Register } from 'claude-code'

const ID_RE = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/
const SESSION_RE = /^[0-9a-fA-F-]{36}$/
const MAX_AGE_MS = 2000
const MAX_FUTURE_MS = 60000
const POLL_MS = 1000
const RESUME_MS = 24 * 60 * 60 * 1000

export type Accepted = { seq: number; id: string }

/** inbox の 1 ファイルを検証する。受け付けるのは新しい seq と正しい ID だけ。 */
export function acceptRequest(raw: string, lastSeq: number, now: number): Accepted | null {
  let row: unknown
  try {
    row = JSON.parse(raw)
  } catch {
    return null
  }
  if (typeof row !== 'object' || row === null || Array.isArray(row)) return null
  if (Object.keys(row).sort().join() !== 'id,seq') return null
  const { seq, id } = row as { seq?: unknown; id?: unknown }
  if (typeof seq !== 'number' || !Number.isSafeInteger(seq) || seq <= 0 || seq <= lastSeq) return null
  if (now - seq > MAX_AGE_MS || seq - now > MAX_FUTURE_MS) return null
  if (typeof id !== 'string' || !ID_RE.test(id)) return null
  return { seq, id }
}

export type ReceiverDeps = {
  read: () => Promise<string>
  fill: (text: string) => Promise<{ isFilled: boolean }>
  write: (text: string) => Promise<void>
  now: () => Promise<number>
}

/** 1 周期分の受信処理。重なりを防ぎ、seq は fill の前に進める。 */
export function createReceiver(deps: ReceiverDeps, initialSeq: number) {
  let lastSeq = initialSeq
  let isTicking = false
  return async () => {
    if (isTicking) return
    isTicking = true
    try {
      const accepted = acceptRequest(await deps.read(), lastSeq, await deps.now())
      if (!accepted) return
      lastSeq = accepted.seq
      let isFilled = false
      try {
        isFilled = (await deps.fill(`/${accepted.id} `)).isFilled
      } catch {
        isFilled = false
      }
      await deps.write(JSON.stringify({ seq: accepted.seq, isFilled }))
    } finally {
      isTicking = false
    }
  }
}

/** 入力の先頭が /id の形なら id だけを返す。本文は読まない。 */
export function commandFromPrompt(text: string): string | null {
  const match = /^\/([A-Za-z0-9][A-Za-z0-9_.:-]{0,127})(?:\s|$)/.exec(text)
  return match?.[1] ?? null
}

/** album.json に載っているカードの呼び出しだけを返す。Skill でない語は記録しない。 */
export function knownCommand(text: string, albumRaw: string): string | null {
  const skill = commandFromPrompt(text)
  if (!skill) return null
  try {
    const cards = (JSON.parse(albumRaw) as { cards?: unknown }).cards
    return typeof cards === 'object' && cards !== null && Object.hasOwn(cards, skill) ? skill : null
  } catch {
    return null
  }
}

type ToolAnswer = { deny?: string; isError?: boolean; text?: string }

/** 内蔵ブラウザで開けたときだけ true を返す。呼べない、拒否された、読み込めなかったは false。 */
export async function openInPane(url: string, call: (tool: string, input: Record<string, unknown>) => Promise<ToolAnswer>): Promise<boolean> {
  try {
    const opened = await call('mcp__Claude_Browser__preview_start', { url })
    if (opened.deny !== undefined || opened.isError || /"navOk"\s*:\s*false/.test(opened.text ?? '')) return false
  } catch {
    return false
  }
  try {
    await call('mcp__ccd_view__show_pane', { pane: 'preview' })
  } catch {
    // 開けてはいるので、ペインの前面化だけの失敗は既定のブラウザへ回さない
  }
  return true
}

const PLUGIN = 'skill-album'
const ALBUM_ORIGIN = 'http://127.0.0.1:47851'

/** 許可するのはこの Mod 自身の呼び出しで、自セッションのアルバムを開くものだけ。 */
export function isOwnPreviewStart(origin: string, input: unknown, session: string): boolean {
  if (origin !== PLUGIN || !SESSION_RE.test(session) || typeof input !== 'object' || input === null) return false
  const { url, name } = input as { url?: unknown; name?: unknown }
  return name === undefined && url === `${ALBUM_ORIGIN}/?s=${session}`
}

/** 許可するのはこの Mod 自身の呼び出しで、内蔵ブラウザのペインを前面に出すものだけ。 */
export function isOwnShowPane(origin: string, input: unknown): boolean {
  if (origin !== PLUGIN || typeof input !== 'object' || input === null) return false
  return (input as { pane?: unknown }).pane === 'preview'
}

let timer: { cancel: () => void } | null = null

const PYTHONS = [['py', '-3'], ['python']]

/** この Mod は Windows だけで動かす。ほかの OS ではスキルの open を使う。 */
async function isWindows($: EngineInterface): Promise<boolean> {
  return (await $.env.get('OS')) === 'Windows_NT'
}

async function claudeHome($: EngineInterface): Promise<string> {
  return (await $.env.get('CLAUDE_CONFIG_DIR')) || `${await $.env.get('USERPROFILE')}\\.claude`
}

async function dataDir($: EngineInterface): Promise<string> {
  return `${await claudeHome($)}\\skill-album`
}

/** 動く Python で launcher を走らせる。どれも起動できなければ null。 */
async function runLauncher($: EngineInterface, args: string[], cwd: string): Promise<string | null> {
  const launcher = `${$.plugin.root}\\skills\\tools\\scripts\\launcher.py`
  for (const python of PYTHONS) {
    try {
      const ran = await $.process.run([...python, '-I', launcher, ...args], { cwd, timeoutMs: 15000 })
      if (ran.stdout.trim()) return ran.stdout
    } catch {
      // 次の候補を試す
    }
  }
  return null
}

async function startPolling($: EngineInterface, session: string): Promise<void> {
  timer?.cancel()
  const dir = await dataDir($)
  const tick = createReceiver(
    {
      read: () => $.fs.read(`${dir}\\inbox\\${session}.json`),
      fill: text => $.prompt.fill({ text, mode: 'append' }),
      write: text => $.fs.write(`${dir}\\outbox\\${session}.json`, text),
      now: () => $.clock.now(),
    },
    await $.clock.now(),
  )
  timer = $.clock.every(POLL_MS, () => {
    void tick().catch(() => {})
  })
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    if (!(await isWindows($))) return started
    await $.command.register({ name: 'skill-album', description: 'Skill Album を開く', immediate: true })
    const session = await $.session.id()
    const saved = await $.store.get(`active:${session}`)
    if (typeof saved === 'number' && (await $.clock.now()) - saved < RESUME_MS) {
      await startPolling($, session)
    }
    return started
  })

  on('tool.check', { tool: 'mcp__Claude_Browser__preview_start' }, async ($, e, next) =>
    isOwnPreviewStart(next.origin.plugin, e.input, await $.session.id()) ? { decision: 'allow' } : next(e),
  ).catch(($, e, next) => next(e))

  on('tool.check', { tool: 'mcp__ccd_view__show_pane' }, async (_$, e, next) =>
    isOwnShowPane(next.origin.plugin, e.input) ? { decision: 'allow' } : next(e),
  ).catch(($, e, next) => next(e))

  on('command.run', { command: 'skill-album' }, async $ => {
    const session = await $.session.id()
    if (!SESSION_RE.test(session)) return { text: '起動できません: セッション ID が不正です' }
    const home = (await $.env.get('USERPROFILE')) ?? $.plugin.root
    const dir = await dataDir($)
    const isFirstRun = !(await $.fs.exists(`${dir}\\album.json`))
    const args = ['--session', session, '--data-dir', dir, '--claude-home', await claudeHome($)]
    const output = await runLauncher($, [...args, '--no-browser'], home)
    if (output === null) return { text: '起動できません: Python 3 が見つかりません' }
    let status = ''
    let url = ''
    try {
      const parsed = JSON.parse(output.trim().split('\n').pop() ?? '')
      status = parsed.status
      url = parsed.url ?? ''
    } catch {
      return { text: '起動できません' }
    }
    if (status === 'port-busy') return { text: '起動できません: ポートが使用中です' }
    if (status !== 'ok') return { text: '起動できません' }
    await $.store.set(`active:${session}`, await $.clock.now())
    await startPolling($, session)
    const isInPane = await openInPane(url, (tool, input) =>
      $.tool.call({ tool, consent: 'The user ran /skill-album', ...input } as Parameters<typeof $.tool.call>[0]),
    )
    if (!isInPane && (await runLauncher($, args, home)) === null) {
      return { text: '起動できません: Python 3 が見つかりません' }
    }
    if (isFirstRun) await $.prompt.fill({ text: '/skill-album:tools classify', mode: 'append' })
    return { text: 'Skill Album を開きました' }
  })

  on('prompt.submit', async ($, e, next) => {
    if (commandFromPrompt(e.text)) {
      void (async () => {
        if (!(await isWindows($))) return
        const dir = await dataDir($)
        const skill = knownCommand(e.text, await $.fs.read(`${dir}\\album.json`))
        if (!skill) return
        const session = await $.session.id()
        await $.fs.write(`${dir}\\usage-inbox\\${session}.json`, JSON.stringify({ seq: await $.clock.now(), skill }))
      })().catch(() => {})
    }
    return next(e)
  }).catch(($, e, next) => next(e))
}
