//! A synthetic, local-only demonstration. Imported source is never executed.
use std::error::Error;
use std::io::Write;
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};

use casita::experimental::{
    ClosureStatus, GitClosureImportError, GitObjectFormat, GitObjectKind, MemoryBlobStore,
    MemoryMetadataStore, MetadataStore, Repository, git_object_key,
};
use casita::import::GitClosureImport;
use casita::{ObjectKey, RootName};
use futures::TryStreamExt;
use tokio::io::AsyncReadExt;

type Result<T> = std::result::Result<T, Box<dyn Error>>;

fn create_output_directory(root: &Path, path: &Path) -> Result<PathBuf> {
    let parts: Vec<_> = path.components().collect();
    if parts.len() != 2
        || parts[0] != Component::Normal("output".as_ref())
        || !matches!(parts[1], Component::Normal(_))
    {
        return Err("choose a relative output/<new-directory> path from the demo root".into());
    }
    let parent = root.join("output");
    match std::fs::symlink_metadata(&parent) {
        Ok(metadata) if metadata.is_dir() && !metadata.file_type().is_symlink() => {}
        Ok(_) => return Err("output must be a directory, not a symlink".into()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            std::fs::create_dir(&parent)?;
        }
        Err(error) => return Err(error.into()),
    }
    let destination = root.join(path);
    // create_dir fails for an existing directory, file or symlink; never overwrite.
    std::fs::create_dir(&destination)?;
    Ok(destination)
}

fn output_directory() -> Result<PathBuf> {
    let mut args = std::env::args_os().skip(1);
    let flag = args.next();
    let destination = args.next();
    if flag.as_deref() != Some("--output".as_ref()) || args.next().is_some() {
        return Err("usage: casita-native-git-context --output output/<new-directory>".into());
    }
    let destination = destination.ok_or("missing --output directory")?;
    create_output_directory(&std::env::current_dir()?, Path::new(&destination))
}

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
    let output = output_directory()?;
    let source = tempfile::Builder::new()
        .prefix("synthetic-git-")
        .tempdir_in(&output)?;
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

    drop(payload);
    drop(second);
    drop(repository);
    casita::experimental::flush_repository_leases().await?;

    // Restore only our synthetic helper so a fresh disk store can import the tree.
    // This is a separate import, not a transfer from the earlier memory store.
    assert_eq!(write_blob(b"# synthetic shared helper\n")?, helper);
    let store = output.join("durable-store");
    let name = RootName::try_from("synthetic/context-v2")?;
    let garbage_key;
    {
        let local = Repository::local(&store).await?;
        let imported = local
            .import(GitClosureImport::new(
                source.path().join("objects"),
                [second_root.clone()],
            ))
            .await?;
        assert_eq!(imported.report.imported_objects, 3);
        let session = local.mutation_session().await?;
        // The import reader stays alive until the named application root commits.
        session
            .publish_rooted(Vec::new(), name.clone(), second_root.clone())
            .await?;
        drop(session);
        drop(imported);

        let session = local.mutation_session().await?;
        let garbage = session
            .stage_blob(b"synthetic unrooted GC control\n")
            .await?;
        garbage_key = garbage.record().key().clone();
        session.publish_unrooted(vec![garbage]).await?;
        drop(session);
        local.flush().await?;
        println!("PASS durable publish: named root committed before releasing import reader");
    }
    casita::experimental::flush_repository_leases().await?;
    source.close()?;

    // Reopen with no import reader, session, source repository or payload stream.
    let reopened = Repository::local(&store).await?;
    assert_eq!(
        reopened.metadata().snapshot().await?.root(&name).await?,
        Some(second_root.clone())
    );
    assert!(
        reopened
            .metadata()
            .snapshot()
            .await?
            .object(&garbage_key)
            .await?
            .is_some()
    );
    println!("PASS durable reopen: named root and unrooted control survive closing handles");

    // Collect before opening any reader that could protect the selected closure.
    let collected = reopened.collect().await?;
    assert_eq!(collected.removed.logical_objects, 1);
    assert!(
        reopened
            .metadata()
            .snapshot()
            .await?
            .object(&garbage_key)
            .await?
            .is_none()
    );
    assert!(matches!(
        reopened.verify_closure(&second_root).await?,
        ClosureStatus::Complete { objects: 3 }
    ));
    println!("PASS durable collection: unrooted control removed; named tree remains complete");

    let retained = reopened
        .import(GitClosureImport::new(
            output.join("removed-git-source"),
            [second_root],
        ))
        .await?;
    assert_eq!(retained.report.imported_objects, 0);
    assert_eq!(retained.report.reused_objects, 1);
    assert_eq!(retained.report.source_bytes, 0);
    let (_, mut payload) = retained
        .reader
        .open_payload(&key(GitObjectKind::Blob, &second_context)?)
        .await?
        .ok_or("missing durable context payload")?;
    let mut restored = Vec::new();
    payload.read_to_end(&mut restored).await?;
    assert_eq!(restored, second_bytes);
    println!("PASS durable readback: exact context restored with original Git source removed");
    drop(payload);
    drop(retained);
    reopened.flush().await?;
    println!("Synthetic local example complete. No source execution or artifact transport.");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> tempfile::TempDir {
        let output = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../output");
        std::fs::create_dir_all(&output).unwrap();
        tempfile::tempdir_in(output).unwrap()
    }

    #[test]
    fn fresh_output_is_created_but_existing_evidence_is_preserved() {
        let root = fixture();
        let path = Path::new("output/trial");
        let destination = create_output_directory(root.path(), path).unwrap();
        let sentinel = destination.join("evidence.txt");
        std::fs::write(&sentinel, b"synthetic existing evidence").unwrap();
        assert!(create_output_directory(root.path(), path).is_err());
        assert_eq!(
            std::fs::read(sentinel).unwrap(),
            b"synthetic existing evidence"
        );
    }

    #[test]
    fn outside_and_nested_paths_reject_before_creating_output() {
        let root = fixture();
        let absolute = root.path().join("output/absolute");
        for path in [
            Path::new("outside"),
            Path::new("output"),
            Path::new("output/../outside"),
            Path::new("output/nested/trial"),
            absolute.as_path(),
        ] {
            assert!(create_output_directory(root.path(), path).is_err());
        }
        assert!(!root.path().join("output").exists());
        assert!(!root.path().join("outside").exists());
    }

    #[cfg(unix)]
    #[test]
    fn symlinked_output_parent_cannot_redirect_artifacts() {
        let root = fixture();
        let outside = root.path().join("outside");
        std::fs::create_dir(&outside).unwrap();
        std::os::unix::fs::symlink(&outside, root.path().join("output")).unwrap();
        assert!(create_output_directory(root.path(), Path::new("output/trial")).is_err());
        assert!(!outside.join("trial").exists());
    }

    #[cfg(unix)]
    #[test]
    fn existing_output_symlink_is_preserved() {
        let root = fixture();
        std::fs::create_dir(root.path().join("output")).unwrap();
        let outside = root.path().join("outside");
        std::fs::create_dir(&outside).unwrap();
        let link = root.path().join("output/trial");
        std::os::unix::fs::symlink(&outside, &link).unwrap();
        assert!(create_output_directory(root.path(), Path::new("output/trial")).is_err());
        assert!(
            std::fs::symlink_metadata(link)
                .unwrap()
                .file_type()
                .is_symlink()
        );
        assert_eq!(std::fs::read_dir(outside).unwrap().count(), 0);
    }
}
