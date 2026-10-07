//! Exercise failure boundaries using the real coordinator and worker processes.
use std::io::Write;
use std::path::Path;
use std::process::{Command, Output, Stdio};

fn fixture() -> tempfile::TempDir {
    let output = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../output");
    std::fs::create_dir_all(&output).unwrap();
    tempfile::tempdir_in(output).unwrap()
}

fn binary(root: &Path) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_casita-native-git-context"));
    command.current_dir(root);
    command
}

fn worker(root: &Path, mode: &str) -> Output {
    let mut child = binary(root)
        .arg(mode)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all(b"output/trial\n")
        .unwrap();
    child.wait_with_output().unwrap()
}

#[test]
fn publisher_failure_stops_before_verifier_or_completion() {
    let root = fixture();
    let result = binary(root.path())
        .args(["--output", "output/trial"])
        // Git cannot start, even though the coordinator can start its own binary.
        .env("PATH", "")
        .output()
        .unwrap();
    assert!(!result.status.success());
    let stdout = String::from_utf8(result.stdout).unwrap();
    assert!(!stdout.contains("PASS process exit"));
    assert!(!stdout.contains("PASS durable reopen"));
    assert!(!stdout.contains("Synthetic local example complete"));
    assert!(!root.path().join("output/trial/durable-store").exists());
    assert!(!root.path().join("output/trial/restart-state.txt").exists());
}

#[test]
fn wrong_root_receipt_rejects_before_collection() {
    let root = fixture();
    std::fs::create_dir_all(root.path().join("output/trial")).unwrap();
    let published = worker(root.path(), "--internal-publish");
    assert!(published.status.success(), "{published:?}");
    let receipt = root.path().join("output/trial/restart-state.txt");
    let original = std::fs::read_to_string(&receipt).unwrap();
    let keys: Vec<_> = original.lines().collect();
    assert_eq!(keys.len(), 3);
    // Substitute the unrooted control for the expected tree identity.
    std::fs::write(&receipt, format!("{}\n{}\n{}\n", keys[2], keys[1], keys[2])).unwrap();
    let rejected = worker(root.path(), "--internal-verify");
    assert!(!rejected.status.success());
    assert!(
        !String::from_utf8(rejected.stdout)
            .unwrap()
            .contains("PASS durable collection")
    );

    // A subsequent correct verifier requires the control to still exist, proving
    // the failed verifier did not collect before validating its expected root.
    std::fs::write(receipt, original).unwrap();
    let verified = worker(root.path(), "--internal-verify");
    assert!(verified.status.success(), "{verified:?}");
    let stdout = String::from_utf8(verified.stdout).unwrap();
    assert!(stdout.contains("PASS durable collection"));
    assert!(stdout.contains("PASS durable readback"));
}
