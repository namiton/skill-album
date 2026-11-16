# Skill Album

Claude Code に入っている Skill を 1 枚ずつのカードにして、革のバインダーで眺めるアルバムです。カードの説明文は Claude が SKILL.md から書き、絵は手元の画像生成ツールで作って貼れます。データはすべて手元の PC に置かれ、外へは送りません。

## 必要なもの

- Claude Code
- Python 3.9 以上（標準ライブラリだけで動きます。追加のパッケージは不要）

## 入れ方

Claude Code で次の 2 つを実行します。

```
/plugin marketplace add namiton/skill-album
/plugin install skill-album@skill-album
```

## 使い方

| コマンド | すること |
|---|---|
| `/skill-album` | アルバムを開く（Windows のデスクトップ版のみ。内蔵ブラウザで開き、カードの「使う」で入力欄に `/スキル名` が入る） |
| `/skill-album-tools open` | アルバムを既定のブラウザで開く（全 OS） |
| `/skill-album-tools classify` | カードをフォルダへ自動で分ける |
| `/skill-album-tools texts` | カードの説明文とフレーバーテキストを書く |
| `/skill-album-tools prompts` | カードの絵の指示書（`art-prompts.md`）を作る |

初めて開いたときは、カードがまだフォルダに分かれていません。`classify` を一度実行してください。

### カードの絵

`prompts` で作った指示書を手元の画像生成ツールに渡し、できた PNG / JPEG / WebP をカードの拡大画面へドロップするか貼り付けます。

### 置き場所

- アルバムのデータ: `~/.claude/skill-album/`（`CLAUDE_CONFIG_DIR` を設定しているときはその中の `skill-album/`）
- サーバー: `127.0.0.1:47851` だけで待ち受け、10 分使わないと自動で止まります

### Windows 以外

`/skill-album` コマンドと内蔵ブラウザでの表示は Windows のデスクトップ版だけです。ほかの環境では `/skill-album-tools open` で開き、「使う」を押すと `/スキル名` がクリップボードにコピーされます。

## ライセンス

MIT License。ただし効果音は OtoLogic の CC BY 4.0 素材です（使用した音素材：OtoLogic(https://otologic.jp) CC BY 4.0）。詳しくは [NOTICE.md](NOTICE.md)。

---

## English

Skill Album turns the Claude Code skills you have installed into cards in a leather binder. Claude writes the card texts from each SKILL.md; you can add card art made with any image generator. Everything stays on your machine. The UI is in Japanese.

Requirements: Claude Code and Python 3.9+ (standard library only).

```
/plugin marketplace add namiton/skill-album
/plugin install skill-album@skill-album
```

Run `/skill-album-tools open` to open the album in your browser (on Windows desktop, `/skill-album` opens it in the built-in browser pane), then `/skill-album-tools classify` once to sort the cards into folders.

License: MIT, except the sound effects, which are from OtoLogic under CC BY 4.0. See [NOTICE.md](NOTICE.md).
