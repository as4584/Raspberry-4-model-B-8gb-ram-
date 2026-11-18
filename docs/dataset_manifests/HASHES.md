# dataset_v1 file hashes

Manifests list the sha256 of the LF-encoded JSONL (generator after the 2026-11-18 byte-exact write fix).
The desktop R3/R4 runs read CRLF copies of the identical content (pre-fix Windows text-mode write);
`tr -d '\r'` of each desktop file reproduces the manifest hash exactly (verified 2026-11-18).

| file | manifest (LF) sha256 | desktop CRLF copy sha256 (appears in run configs) |
|---|---|---|
| train_diverse | fb3c0268ecc221ef72035d71bd0c79536746971bed2af95fe2d904ca494b8bac | 08e2f4e11d33eabb14579ba17f0e7b9d320be08d597e00888199f3ea6dd9be30 |
| train_narrow | ece4398b1872b3834ec56c2cb71982d4c20d518d62d8e1d26cbef49fe5accb22 | f0c88620376be5ac5670c5e64c09bfeeb9ebf4882de7fd2b51b8bb72a16c920d |
| dev | d9afc6e095021653c884db9a1cacf12f6014eb62ed8d51424cce7977efba5b28 | f96a85a902f894b20fe32e6c153eaa7ff0bc8e64e452cbcd53dfac717b365ea1 |

Regenerate: `python -m lab.datagen --split train --seed 20261118 --n 4000 [--mode narrow] --out <file>`
and `python -m lab.datagen --split dev --seed 20261118 --n 600 --out <file>`.
