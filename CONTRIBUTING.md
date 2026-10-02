# Contributing to txinTrade

Thank you for helping improve txinTrade. Bug reports, reproducible issues, and
focused pull requests are welcome.

## Before you start

- For larger changes, open an issue first so the approach can be agreed before
  you spend time on it.
- txinTrade provides analysis and read-only position imports. Changes that place
  orders, move funds, or weaken credential protection will not be accepted.
- Never include API keys, exchange credentials, model account tokens, local
  databases, or personal trading data in issues, commits, or test fixtures.

## Contributor License Agreement

Every contributor must accept the [Contributor License Agreement](CLA.md)
before a pull request can be merged. You keep the copyright in your work; the
CLA lets the maintainer distribute it under the AGPL and under other license
terms, including commercial licenses and hosted services.

When you open your first pull request, the CLA check posts instructions. Sign by
replying with:

```
I have read the CLA Document and I hereby sign the CLA
```

You only need to sign once.

## Development

Follow the setup in the [README](README.md), then run the checks listed under
**Verification** before opening a pull request. Add or update tests for behavior
you change, and keep Traditional Chinese and English UI text in sync
(`pnpm check:bilingual`).

## License

By contributing, you agree that your contributions are licensed as described in
the CLA, and that the project is distributed under the
[GNU Affero General Public License v3](LICENSE). The txinTrade name and logos are
covered by the [trademark policy](TRADEMARKS.md).
