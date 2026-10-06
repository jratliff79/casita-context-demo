//! A synthetic, local-only demonstration. Imported source is never executed.
use std::error::Error;
use std::io::Write;
use std::path::Path;
use std::process::{Command, Stdio};

use casita::ObjectKey;
use casita::experimental::{
    ClosureStatus, GitClosureImportError, GitObjectFormat, GitObjectKind, MemoryBlobStore,
    MemoryMetadataStore, MetadataStore, Repository, git_object_key,
};
use casita::import::GitClosureImport;
use futures::TryStreamExt;
use tokio::io::AsyncReadExt;

type Result<T> = std::result::Result<T, Box<dyn Error>>;

fn git(source: &Path, args: &[&str], input: &[u8]) -> Result<String> {
    // Do not inherit Git directory, object-store, config or alternate overrides.
    let mut child = Command::new("git")
        .env_clear()
        .env("PATH", std::env::var_os("PATH").unwrap_or_default())
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_CONFIG_GLOBAL", "/dev/null")
        .arg("-C")
        .arg(source)
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    child
        .stdin
        .take()
        .ok_or("missing Git stdin")?
        .write_all(input)?;
    let result = child.wait_with_output()?;
    if !result.status.success() {
        return Err(format!("Git failed: {}", String::from_utf8_lossy(&result.stderr)).into());
    }
    Ok(String::from_utf8(result.stdout)?.trim().to_owned())
}

fn key(kind: GitObjectKind, oid: &str) -> Result<ObjectKey> {
    Ok(git_object_key(
        GitObjectFormat::Sha1,
        kind,
        data_encoding::HEXLOWER.decode(oid.as_bytes())?,
    )?)
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<()> {
    let source = tempfile::tempdir()?;
    git(
        source.path(),
        &[
            "init",
            "--bare",
            "--object-format=sha1",
            "--template=",
            "-q",
        ],
        b"",
    )?;
    let write_blob = |bytes: &[u8]| git(source.path(), &["hash-object", "-w", "--stdin"], bytes);
    let helper = write_blob(b"# synthetic shared helper\n")?;
    let first_context = write_blob(b"synthetic context revision one\n")?;
    let excluded = write_blob(b"SYNTHETIC unselected sibling, never a credential\n")?;
    let make_tree = |context: &str| {
        git(
            source.path(),
            &["mktree"],
            format!("100644 blob {context}\tcontext.txt\n100644 blob {helper}\thelper.py\n")
                .as_bytes(),
        )
    };
    let first_oid = make_tree(&first_context)?;
    let first_root = key(GitObjectKind::Tree, &first_oid)?;
    let surrounding_oid = git(
        source.path(),
        &["mktree"],
        format!(
            "040000 tree {first_oid}\tselected-context\n100644 blob {excluded}\tunselected-note.txt\n"
        )
        .as_bytes(),
    )?;
    let repository = Repository::<MemoryBlobStore, MemoryMetadataStore>::memory()?;
    let first = repository
        .import(GitClosureImport::new(
            source.path().join("objects"),
            [first_root.clone()],
        ))
        .await?;
    assert_eq!(first.report.imported_objects, 3);
    assert_eq!(first.report.reused_objects, 0);
    assert!(
        first
            .reader
            .object(&key(GitObjectKind::Blob, &excluded)?)
            .await?
            .is_none()
    );
    assert!(
        first
            .reader
            .object(&key(GitObjectKind::Tree, &surrounding_oid)?)
            .await?
            .is_none()
    );
    assert!(matches!(
        repository.verify_closure(&first_root).await?,
        ClosureStatus::Complete { objects: 3 }
    ));
    println!("PASS cold: imported 3 objects; surrounding tree and sibling excluded");

    // A complete stored root can be retained without reopening its source.
    let warm = repository
        .import(GitClosureImport::new(
            source.path().join("absent-source"),
            [first_root.clone()],
        ))
        .await?;
    assert_eq!(warm.report.imported_objects, 0);
    assert_eq!(warm.report.reused_objects, 1);
    assert_eq!(warm.report.source_bytes, 0);
    println!("PASS warm: imported 0 objects; reused 1 root; read 0 source bytes");

    let second_bytes = b"synthetic context revision two\n";
    let second_context = write_blob(second_bytes)?;
    let second_root = key(GitObjectKind::Tree, &make_tree(&second_context)?)?;
    // Remove the source helper to prove the importer uses its verified stored copy.
    std::fs::remove_file(
        source
            .path()
            .join("objects")
            .join(&helper[..2])
            .join(&helper[2..]),
    )?;
    let second = repository
        .import(GitClosureImport::new(
            source.path().join("objects"),
            [second_root.clone()],
        ))
        .await?;
    assert_eq!(second.report.imported_objects, 2);
    assert_eq!(second.report.reused_objects, 1);
    assert!(
        second
            .reader
            .object(&key(GitObjectKind::Blob, &excluded)?)
            .await?
            .is_none()
    );
    println!("PASS delta: imported 2 objects; reused the unchanged helper");

    let wrong_type = repository
        .import(GitClosureImport::new(
            source.path().join("objects"),
            [key(GitObjectKind::Tree, &second_context)?],
        ))
        .await;
    assert!(
        matches!(wrong_type, Err(GitClosureImportError::RootKind { .. })),
        "a blob selected as a tree must fail with the root-type error"
    );
    assert!(
        repository
            .metadata()
            .snapshot()
            .await?
            .roots()
            .try_collect::<Vec<_>>()
            .await?
            .is_empty()
    );
    println!("PASS controls: wrong root type rejected; no named roots published");

    drop(first);
    drop(warm);
    casita::experimental::flush_repository_leases().await?;
    repository.collect().await?;
    // Keep `second` alive: its retained reader protects this closure during GC.
    assert!(matches!(
        repository.verify_closure(&second_root).await?,
        ClosureStatus::Complete { objects: 3 }
    ));
    let (_, mut payload) = second
        .reader
        .open_payload(&key(GitObjectKind::Blob, &second_context)?)
        .await?
        .ok_or("missing context payload")?;
    let mut bytes = Vec::new();
    payload.read_to_end(&mut bytes).await?;
    assert_eq!(bytes, second_bytes);
    println!("PASS retention: live reader survives collection; exact payload read back");
    println!("Synthetic local example complete. No source execution or artifact transport.");
    Ok(())
}
