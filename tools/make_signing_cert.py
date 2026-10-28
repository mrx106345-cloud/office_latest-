"""Create the Pryonx code-signing certificate.

The certificate is **self-signed**, which means Windows will not trust it until
the user installs it once by hand. That is a real cost and this script does not
pretend otherwise: it is the right trade only for free software distributed to
people who can be asked to run one extra step. See the trust guide shipped with
the installer.

What it buys over shipping unsigned:

* the publisher reads **Pryonx** instead of "Unknown publisher";
* Windows can tell the file has not been modified since it was signed.

What it does **not** buy: SmartScreen reputation. Only a certificate from a
certificate authority builds that, and even those take days or weeks of real
downloads to accumulate. Expect users to click "More info -> Run anyway"
regardless.

The private key never enters the repository. The default output path is outside
the project and ``.gitignore`` excludes the key wherever it lands.

Usage::

    python tools/make_signing_cert.py --out C:\\Keys\\yourdoc.pfx --password secret
    python tools/make_signing_cert.py --out ... --password ... --days 3650
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

DEFAULT_SUBJECT = "Pryonx"
DEFAULT_ORGANISATION = "Pryonx"
DEFAULT_COUNTRY = "IN"
DEFAULT_DAYS = 3650
#: RSA-3072 is the CA/Browser Forum baseline for code signing.
KEY_SIZE = 3072


def build_certificate(subject: str, organisation: str, country: str, days: int):
    """Return ``(certificate, private_key)`` for a self-signed code signer."""

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=KEY_SIZE)
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, country),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, organisation),
            x509.NameAttribute(NameOID.COMMON_NAME, subject),
        ]
    )

    now = dt.datetime.now(dt.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)  # self-signed: issuer is itself
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))  # tolerate clock skew
        .not_valid_after(now + dt.timedelta(days=days))
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None), critical=True
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.CODE_SIGNING]),
            critical=False,
        )
        # Subject key identifier: lets Windows find the cert in the store by
        # thumbprint, which is what the trust guide tells the user to select.
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    return certificate, key


def write_pfx(path: Path, certificate, key, password: str) -> None:
    """Serialise the certificate and key to a password-protected PKCS#12 file."""

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import pkcs12

    path.parent.mkdir(parents=True, exist_ok=True)
    blob = pkcs12.serialize_key_and_certificates(
        name=b"YourDoc",
        key=key,
        cert=certificate,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(
            password.encode("utf-8") if password else None
        ),
    )
    path.write_bytes(blob)


def write_certificate(path: Path, certificate) -> None:
    """Write the public certificate separately, for publishing as a trust guide."""

    from cryptography.hazmat.primitives import serialization

    path.write_bytes(
        certificate.public_bytes(serialization.Encoding.DER)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create the Pryonx self-signed code-signing certificate."
    )
    parser.add_argument(
        "--out", required=True, type=Path,
        help="where to write the .pfx (keep it OUTSIDE the repository)",
    )
    parser.add_argument(
        "--cert-out", type=Path, default=None,
        help="also write the public .cer here, for the user trust guide",
    )
    parser.add_argument("--password", default="", help="PFX password")
    parser.add_argument("--subject", default=DEFAULT_SUBJECT)
    parser.add_argument("--organisation", default=DEFAULT_ORGANISATION)
    parser.add_argument("--country", default=DEFAULT_COUNTRY)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument(
        "--force", action="store_true",
        help="overwrite an existing file instead of refusing",
    )
    args = parser.parse_args(argv)

    out: Path = args.out
    if out.exists() and not args.force:
        print(f"refusing to overwrite {out} (pass --force to replace it)", file=sys.stderr)
        return 1

    certificate, key = build_certificate(
        args.subject, args.organisation, args.country, args.days
    )
    write_pfx(out, certificate, key, args.password)

    if args.cert_out:
        write_certificate(args.cert_out, certificate)

    from cryptography.hazmat.primitives import hashes

    fingerprint = certificate.fingerprint(hashes.SHA256()).hex().upper()
    print(f"wrote {out}")
    if args.cert_out:
        print(f"wrote {args.cert_out}")
    print(f"  subject   : {certificate.subject.rfc4514_string()}")
    print(f"  thumbprint : {fingerprint}")
    print(f"  valid until: {certificate.not_valid_after_utc.date()}")
    print()
    print("Next:")
    print(f"  set YOURDOC_SIGN_PFX={out}")
    if args.password:
        print("  set YOURDOC_SIGN_PFX_PASSWORD=<the password you chose>")
    print("  then run python build_installer.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())