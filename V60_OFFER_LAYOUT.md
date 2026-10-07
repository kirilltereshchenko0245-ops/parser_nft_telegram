# V60 offer layout fix

- NFT preview and interactive offer are sent as one Telegram message.
- The preview is rendered above the offer text with `LinkPreviewOptions(show_above_text=True)`.
- The gift title in the offer text is an HTML link to the canonical NFT URL.
- Offer lifetime is 6 hours; the initial display is rendered as `5ч 59м`.
- Accept/Decline keyboard remains the proven V23 keyboard.
- Per-user transfer profile remains `tg://send_gift?to=username`.
- Final accepted state does not attach a second NFT preview.
