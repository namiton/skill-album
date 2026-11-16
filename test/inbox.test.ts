import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { acceptRequest, commandFromPrompt, createReceiver, isOwnPreviewStart, isOwnShowPane, knownCommand, openInPane } from '../hooks/register.ts'

const SESSION = '00000000-0000-0000-0000-000000000000'
const URL_WITH_SESSION = `http://127.0.0.1:47851/?s=${SESSION}`

test('Mod 自身が自セッションのアルバムを開く呼び出しだけを許可する', () => {
  assert.equal(isOwnPreviewStart('skill-album', { url: URL_WITH_SESSION }, SESSION), true)
})

test('他の呼び出し元、別の URL、別のセッション、name 指定は許可しない', () => {
  assert.equal(isOwnPreviewStart('engine', { url: URL_WITH_SESSION }, SESSION), false)
  assert.equal(isOwnPreviewStart('other-mod', { url: URL_WITH_SESSION }, SESSION), false)
  const urls = [
    'https://example.com/',
    'http://127.0.0.1:47851/',
    'http://127.0.0.1:47822/?s=' + SESSION,
    'http://localhost:47851/?s=' + SESSION,
    URL_WITH_SESSION + '&x=1',
    'http://127.0.0.1:47851/?s=11111111-1111-1111-1111-111111111111',
    5,
    undefined,
  ]
  for (const url of urls) {
    assert.equal(isOwnPreviewStart('skill-album', { url }, SESSION), false, String(url))
  }
  assert.equal(isOwnPreviewStart('skill-album', { url: URL_WITH_SESSION, name: 'x' }, SESSION), false)
  assert.equal(isOwnPreviewStart('skill-album', null, SESSION), false)
  assert.equal(isOwnPreviewStart('skill-album', { url: 'http://127.0.0.1:47851/?s=x' }, 'x'), false)
})

test('前面化は Mod 自身のペイン指定だけを許可する', () => {
  assert.equal(isOwnShowPane('skill-album', { pane: 'preview' }), true)
  assert.equal(isOwnShowPane('engine', { pane: 'preview' }), false)
  assert.equal(isOwnShowPane('skill-album', { pane: 'file', path: 'a' }), false)
  assert.equal(isOwnShowPane('skill-album', null), false)
})

test('内蔵ブラウザへ ?s= 付きの URL を渡し、開けたらペインを前面に出す', async () => {
  const calls: [string, Record<string, unknown>][] = []
  const isOpened = await openInPane(URL_WITH_SESSION, async (tool, input) => {
    calls.push([tool, input])
    return { text: '{"navOk": true}' }
  })
  assert.equal(isOpened, true)
  assert.deepEqual(calls, [
    ['mcp__Claude_Browser__preview_start', { url: URL_WITH_SESSION }],
    ['mcp__ccd_view__show_pane', { pane: 'preview' }],
  ])
})

test('呼べない、拒否、エラー、読み込み失敗は開けていない扱いにする', async () => {
  const answers: (() => Promise<{ deny?: string; isError?: boolean; text?: string }>)[] = [
    () => Promise.reject(new Error('no such tool')),
    async () => ({ deny: 'no' }),
    async () => ({ isError: true, text: 'failed' }),
    async () => ({ text: '{\n  "navOk": false\n}\nBrowser pane opened at about:blank' }),
  ]
  for (const answer of answers) {
    assert.equal(await openInPane(URL_WITH_SESSION, answer), false)
  }
})

test('前面化だけ失敗しても開けた扱いにする', async () => {
  const isOpened = await openInPane(URL_WITH_SESSION, async tool => {
    if (tool === 'mcp__ccd_view__show_pane') throw new Error('no such tool')
    return { text: '{"navOk": true}' }
  })
  assert.equal(isOpened, true)
})

const NOW = 1_000_000
const source = readFileSync(new URL('../hooks/register.ts', import.meta.url), 'utf8')

const json = (value: unknown) => JSON.stringify(value)

test('正しい依頼だけを受け付ける', () => {
  assert.deepEqual(acceptRequest(json({ seq: NOW, id: 'git-ops:commit' }), 0, NOW), { seq: NOW, id: 'git-ops:commit' })
})

test('不正な ID を拒否する', () => {
  const ids = ['', 'git-ops:commit\n/other', 'a b', '-x', '.x', 'x'.repeat(129), '..', 'a/b', '/x', '../x', 'a\u0007b', 'あ', 5, null]
  for (const id of ids) {
    assert.equal(acceptRequest(json({ seq: NOW, id }), 0, NOW), null, String(id))
  }
  assert.notEqual(acceptRequest(json({ seq: NOW, id: 'x'.repeat(128) }), 0, NOW), null)
})

test('不正な seq を拒否する', () => {
  for (const seq of [NOW - 1, 0, -1, 1.5, '1', null, Number.MAX_SAFE_INTEGER + 2]) {
    assert.equal(acceptRequest(json({ seq, id: 'a' }), NOW - 1, NOW), null, String(seq))
  }
  assert.equal(acceptRequest(json({ seq: NOW, id: 'a' }), NOW, NOW), null)
  assert.equal(acceptRequest(json({ seq: NOW - 2001, id: 'a' }), 0, NOW), null)
  assert.notEqual(acceptRequest(json({ seq: NOW - 2000, id: 'a' }), 0, NOW), null)
  assert.equal(acceptRequest(json({ seq: NOW + 60001, id: 'a' }), 0, NOW), null)
})

test('壊れた内容、キー欠落、余分なキーを拒否する', () => {
  for (const raw of ['{"seq":', 'null', '[]', json({ seq: NOW }), json({ id: 'a' }), json({ seq: NOW, id: 'a', text: 'x' })]) {
    assert.equal(acceptRequest(raw, 0, NOW), null, raw)
  }
})

test('入力の先頭の /id だけを取り出す', () => {
  assert.equal(commandFromPrompt('/git-ops:commit 説明の本文'), 'git-ops:commit')
  assert.equal(commandFromPrompt('/teach'), 'teach')
  assert.equal(commandFromPrompt('本文 /teach'), null)
  assert.equal(commandFromPrompt('/usr/bin/x'), null)
  assert.equal(commandFromPrompt(''), null)
})

test('Skill でない語は記録しない', () => {
  const album = json({ cards: { teach: {} } })
  assert.equal(knownCommand('/teach 本文', album), 'teach')
  assert.equal(knownCommand('/秘密の語', album), null)
  assert.equal(knownCommand('/model', album), null)
  assert.equal(knownCommand('/constructor', album), null)
  assert.equal(knownCommand('/teach', '{broken'), null)
})

test('自動送信を呼ばない', () => {
  assert.equal(/\$\.prompt\.submit|\.submit\(/.test(source), false)
})

function harness(raw: string, options: { fill?: () => Promise<{ isFilled: boolean }> } = {}) {
  const fills: string[] = []
  const writes: string[] = []
  const tick = createReceiver(
    {
      read: async () => raw,
      fill: async text => {
        fills.push(text)
        return options.fill ? options.fill() : { isFilled: true }
      },
      write: async text => {
        writes.push(text)
      },
      now: async () => NOW,
    },
    NOW - 1000,
  )
  return { tick, fills, writes }
}

test('同じ依頼を何周しても追記は 1 回', async () => {
  const { tick, fills, writes } = harness(json({ seq: NOW, id: 'a' }))
  for (let i = 0; i < 10; i += 1) await tick()
  assert.deepEqual(fills, ['/a '])
  assert.deepEqual(writes, [json({ seq: NOW, isFilled: true })])
})

test('周期が重なっても追記は 1 回', async () => {
  let release: (value: { isFilled: boolean }) => void = () => {}
  const { tick, fills } = harness(json({ seq: NOW, id: 'a' }), {
    fill: () => new Promise(resolve => { release = resolve }),
  })
  const first = tick()
  await new Promise(resolve => setImmediate(resolve))
  await tick()
  await tick()
  release({ isFilled: true })
  await first
  await tick()
  assert.equal(fills.length, 1)
})

test('fill の拒否と例外は isFilled false を書き、再追記しない', async () => {
  for (const fill of [async () => ({ isFilled: false }), async () => { throw new Error('x') }]) {
    const { tick, fills, writes } = harness(json({ seq: NOW, id: 'a' }), { fill })
    await tick()
    await tick()
    assert.equal(fills.length, 1)
    assert.deepEqual(writes, [json({ seq: NOW, isFilled: false })])
  }
})

test('ポーリング開始前の依頼は無視する', async () => {
  const { tick, fills } = harness(json({ seq: NOW - 1000, id: 'a' }))
  await tick()
  assert.equal(fills.length, 0)
})

test('タイマーは張り直す', () => {
  assert.ok(source.includes('timer?.cancel()'))
  assert.ok(source.indexOf('timer?.cancel()') < source.indexOf('timer = $.clock.every'))
})

test('自分のセッションのファイルだけを読む', () => {
  assert.ok(source.includes('inbox\\\\${session}.json'))
  assert.ok(source.includes('outbox\\\\${session}.json'))
})
