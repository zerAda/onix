# -*- coding: utf-8 -*-
"""Tests du **chiffrement fail-closed** des sauvegardes (scripts/backup.sh, BKP-02).

Le runtime Azure (cf. .planning/RUNTIME-EVIDENCE.md) a confirmé que `backup.sh`
produisait des archives **en clair** (tar froid non chiffré) contenant TOUTES les
données (PII, audit HMAC, docs). On verrouille ici, hors-runtime :

  1. FAIL-CLOSED : sans `ONIX_BACKUP_PASSPHRASE` (ni override DEV), `backup.sh`
     REFUSE de produire un backup (exit != 0, message « FAIL-CLOSED »).
  2. CRYPTO : round-trip openssl AES-256-CBC/PBKDF2 identique à celui du script
     (chiffré != clair ; déchiffré == clair) — preuve que la commande est correcte.

On NE lance PAS Docker (le tar des volumes exige le démon) : le refus fail-closed
intervient AVANT toute opération Docker, et le round-trip crypto se teste seul.
Skip propre si bash/openssl absents (jamais un faux vert).
"""
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BACKUP = os.path.normpath(os.path.join(HERE, "..", "backup.sh"))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))

_BASH = shutil.which("bash")
_OPENSSL = shutil.which("openssl")


@unittest.skipIf(_BASH is None, "bash indisponible (Git Bash requis) — test sauté proprement")
class TestBackupEncrypt(unittest.TestCase):
    def _backups_dirs(self):
        d = os.path.join(REPO, "backups")
        return set(os.listdir(d)) if os.path.isdir(d) else set()

    def test_fail_closed_sans_passphrase(self):
        """Sans passphrase ni override : backup.sh REFUSE (exit != 0, message explicite)."""
        env = dict(os.environ)
        env.pop("ONIX_BACKUP_PASSPHRASE", None)
        env.pop("ONIX_BACKUP_ALLOW_PLAINTEXT", None)
        before = self._backups_dirs()
        proc = subprocess.run(
            [_BASH, BACKUP], capture_output=True, text=True, env=env, timeout=90, cwd=REPO,
        )
        # Nettoyage d'un éventuel dossier backups/<ts> vide créé avant le refus.
        for name in self._backups_dirs() - before:
            shutil.rmtree(os.path.join(REPO, "backups", name), ignore_errors=True)
        self.assertNotEqual(proc.returncode, 0, "backup.sh aurait dû REFUSER sans passphrase.")
        self.assertIn("FAIL-CLOSED", proc.stdout + proc.stderr)

    @unittest.skipIf(_OPENSSL is None, "openssl indisponible — round-trip sauté proprement")
    def test_openssl_roundtrip_identique(self):
        """Round-trip AES-256-CBC/PBKDF2 (même invocation que backup.sh / restore.sh)."""
        env = dict(os.environ)
        env["ONIX_BACKUP_PASSPHRASE"] = "passe-de-test-aes-256-1234"
        content = b"contenu secret onix \x00\x01\x02 PII IBAN audit"
        with tempfile.TemporaryDirectory() as d:
            plain = os.path.join(d, "data.bin")
            enc = os.path.join(d, "data.bin.enc")
            dec = os.path.join(d, "data.bin.dec")
            with open(plain, "wb") as f:
                f.write(content)
            r1 = subprocess.run(
                [_OPENSSL, "enc", "-aes-256-cbc", "-pbkdf2", "-salt",
                 "-pass", "env:ONIX_BACKUP_PASSPHRASE", "-in", plain, "-out", enc],
                env=env, capture_output=True, timeout=30,
            )
            self.assertEqual(r1.returncode, 0, r1.stderr)
            with open(enc, "rb") as f:
                self.assertNotEqual(f.read(), content, "le chiffré ne doit pas égaler le clair.")
            r2 = subprocess.run(
                [_OPENSSL, "enc", "-d", "-aes-256-cbc", "-pbkdf2",
                 "-pass", "env:ONIX_BACKUP_PASSPHRASE", "-in", enc, "-out", dec],
                env=env, capture_output=True, timeout=30,
            )
            self.assertEqual(r2.returncode, 0, r2.stderr)
            with open(dec, "rb") as f:
                self.assertEqual(f.read(), content, "le déchiffré doit restituer le clair.")

    @unittest.skipIf(_OPENSSL is None, "openssl indisponible")
    def test_mauvaise_passphrase_echoue(self):
        """Une passphrase erronée au déchiffrement échoue (intégrité)."""
        content = b"secret"
        with tempfile.TemporaryDirectory() as d:
            plain = os.path.join(d, "d.bin"); enc = os.path.join(d, "d.enc"); dec = os.path.join(d, "d.dec")
            with open(plain, "wb") as f:
                f.write(content)
            e1 = dict(os.environ); e1["ONIX_BACKUP_PASSPHRASE"] = "bonne-passe"
            subprocess.run([_OPENSSL, "enc", "-aes-256-cbc", "-pbkdf2", "-salt",
                            "-pass", "env:ONIX_BACKUP_PASSPHRASE", "-in", plain, "-out", enc],
                           env=e1, capture_output=True, timeout=30, check=True)
            e2 = dict(os.environ); e2["ONIX_BACKUP_PASSPHRASE"] = "MAUVAISE-passe"
            r = subprocess.run([_OPENSSL, "enc", "-d", "-aes-256-cbc", "-pbkdf2",
                                "-pass", "env:ONIX_BACKUP_PASSPHRASE", "-in", enc, "-out", dec],
                               env=e2, capture_output=True, timeout=30)
            self.assertNotEqual(r.returncode, 0, "un déchiffrement avec mauvaise passphrase doit échouer.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
