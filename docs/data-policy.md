# Public repository and data policy

Only engineering source, schemas, synthetic schedules, nonlexical test cues,
empty templates and sanitized apparatus evidence belong in this public repo.
No study vocabulary, codebook, learner package, candidate bank, allocation list,
confirmatory seed, participant record or study recording may enter Git or LFS.
This remains the default after data lock until an explicit disclosure review;
data lock alone does not grant permission to publish.

Keep actual packages in approved encrypted storage and refer to their SHA-256
from private apparatus records. Do not implement that store during Phase 1.
Methodology documents and manuscripts remain read-only outside the repository.
Avoid venue, submission, budget and personal details in public engineering text.

Credentials, account details, device serials, network passwords, hostnames and
absolute local paths belong in ignored `*.local.*` configuration, never examples.
Examples use neutral placeholders. Review screenshots and logs before publishing.
Raw headset/audio/network recordings stay private until separately sanitized.

`tools/repo_guard.py` checks every commit reachable from the current branch for
private paths, common key patterns and malformed/non-LFS binary assets. CI also
tests the guards. Pattern scanning supplements review; it cannot prove the
semantic absence of private study material. Enable GitHub secret scanning and
push protection where available. If a secret is committed, revoke it and follow
repository-owner incident handling; deleting the latest file is insufficient.

Third-party licenses are checked per source revision and asset. Unverified
assets stay out of the public repository and study-use approval remains pending.
The repository asset downloader requires immutable revisions and reviewed hashes.
