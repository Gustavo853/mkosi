# SPDX-License-Identifier: LGPL-2.1+
import textwrap
from collections.abc import Sequence

from mkosi.config import yes_no
from mkosi.context import Context
from mkosi.installer import finalize_package_manager_mounts
from mkosi.installer.rpm import RpmRepository, fixup_rpmdb_location, setup_rpm
from mkosi.mounts import finalize_ephemeral_source_mounts
from mkosi.run import run
from mkosi.sandbox import apivfs_cmd
from mkosi.types import PathString
from mkosi.util import sort_packages


def setup_zypper(context: Context, repos: Sequence[RpmRepository]) -> None:
    config = context.pkgmngr / "etc/zypp/zypp.conf"
    config.parent.mkdir(exist_ok=True, parents=True)

    (context.cache_dir / "cache/zypp").mkdir(exist_ok=True, parents=True)

    # rpm.install.excludedocs can only be configured in zypp.conf so we append
    # to any user provided config file. Let's also bump the refresh delay to
    # the same default as dnf which is 48 hours.
    with config.open("a") as f:
        f.write(
            textwrap.dedent(
                f"""
                [main]
                rpm.install.excludedocs = {yes_no(not context.config.with_docs)}
                repo.refresh.delay = {48 * 60}
                """
            )
        )

    repofile = context.pkgmngr / "etc/zypp/repos.d/mkosi.repo"
    if not repofile.exists():
        repofile.parent.mkdir(exist_ok=True, parents=True)
        with repofile.open("w") as f:
            for repo in repos:
                f.write(
                    textwrap.dedent(
                        f"""\
                        [{repo.id}]
                        name={repo.id}
                        {repo.url}
                        gpgcheck=1
                        enabled={int(repo.enabled)}
                        autorefresh=1
                        keeppackages=1
                        """
                    )
                )

                for i, url in enumerate(repo.gpgurls):
                    f.write("gpgkey=" if i == 0 else len("gpgkey=") * " ")
                    f.write(f"{url}\n")

    setup_rpm(context)


def zypper_cmd(context: Context) -> list[PathString]:
    return [
        "env",
        "ZYPP_CONF=/etc/zypp/zypp.conf",
        "HOME=/",
        "zypper",
        f"--installroot={context.root}",
        "--cache-dir=/var/cache/zypp",
        "--gpg-auto-import-keys" if context.config.repository_key_check else "--no-gpg-checks",
        "--non-interactive",
    ]


def invoke_zypper(
    context: Context,
    verb: str,
    packages: Sequence[str],
    options: Sequence[str] = (),
    apivfs: bool = True,
) -> None:
    with finalize_ephemeral_source_mounts(context.config) as sources:
        run(
            zypper_cmd(context) + [verb, *options, *sort_packages(packages)],
            sandbox=(
                context.sandbox(
                    network=True,
                    options=[
                        "--bind", context.root, context.root,
                        *finalize_package_manager_mounts(context),
                        *sources,
                        "--chdir", "/work/src",
                    ],
                ) + (apivfs_cmd(context.root) if apivfs else [])
            ),
            env=context.config.environment,
        )

    fixup_rpmdb_location(context)


def createrepo_zypper(context: Context) -> None:
    run(["createrepo_c", context.packages],
        sandbox=context.sandbox(options=["--bind", context.packages, context.packages]))

    (context.pkgmngr / "etc/zypp/repos.d").mkdir(parents=True, exist_ok=True)
    (context.pkgmngr / "etc/zypp/repos.d/mkosi-packages.repo").write_text(
        textwrap.dedent(
            """\
            [mkosi-packages]
            name=mkosi-packages
            gpgcheck=0
            enabled=1
            baseurl=file:///work/packages
            autorefresh=0
            priority=50
            """
        )
    )
