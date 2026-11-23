#!/usr/bin/env node
// Tests the gates of .github/workflows/fast-lane.yml without GitHub.
//
// The gates live in one inline `actions/github-script` step, so there is no
// module to import: this reads the script out of the workflow, runs it against
// a fake `github` object and checks what it decided. The fake answers the
// review-thread query the way the API does — a page holds `comments(first: N)`
// nodes and reports `hasNextPage` past them — so a query that reads too few
// comments shows up here as a wrong verdict, not as a passing string match.
//
// Run: node .github/ci/test_fast_lane.mjs
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const WORKFLOW = join(ROOT, '.github', 'workflows', 'fast-lane.yml');
const INDENT = ' '.repeat(12);

// The script is the `script: |` block of the github-script step: everything
// indented past it, up to the next key of the workflow.
function readScript() {
  const lines = readFileSync(WORKFLOW, 'utf8').split('\n');
  const start = lines.findIndex((line) => /^\s{10}script: \|$/.test(line));
  assert.notEqual(start, -1, 'the workflow carries a github-script step');
  const body = [];
  for (const line of lines.slice(start + 1)) {
    if (line.trim() !== '' && !line.startsWith(INDENT)) break;
    body.push(line.slice(INDENT.length));
  }
  assert.ok(body.length > 300, 'the whole script was read out of the workflow');
  return body.join('\n');
}

const HEAD = 'a'.repeat(40);
const OLD_HEAD = 'b'.repeat(40);
const REQUIRED = ['Backend Tests', 'Frontend Checks', 'Helm Checks'];
const GREEN = REQUIRED.map((name) => ({
  name, status: 'completed', conclusion: 'success',
  started_at: '2026-01-01T00:00:00Z',
}));
// CodeRabbit's own check run. Under a rate limit it still comes back
// `completed`, only with `neutral` (rate limited) or `skipped` (paused) and a
// title that says so: nothing but the title tells the two apart from a review.
// A review that found nothing to say ends `success` with "Review completed" in
// the summary and no review object at all (#1612).
const coderabbitCheck = (conclusion, title, summary = '') => ({
  name: 'CodeRabbit', status: 'completed', conclusion,
  output: { title, summary }, started_at: '2026-01-01T00:00:01Z',
});
// What a review looks like: the commit it judged is on the review itself.
const coderabbitReview = (commitId = HEAD) => ({
  user: { login: 'coderabbitai[bot]' }, state: 'COMMENTED', commit_id: commitId,
});
const VERDICT = [
  '<!-- fast-lane: approve -->',
  '',
  'Reviewed, green on this head.',
  '',
  `<!-- sha: ${HEAD} -->`,
].join('\n');
// The same verdict, given for the head a later push replaced.
const STALE_VERDICT = VERDICT.replace(HEAD, OLD_HEAD);
// The lane's refusal note, as the workflow writes it: its own comment, opening
// with the marker, rewritten in place across evaluations.
const REFUSAL = '<!-- fast-lane: refused -->';
const refusalNote = (id = 9001) => ({
  id,
  user: { login: 'github-actions[bot]' },
  body: [
    REFUSAL,
    '',
    'This pull request was not merged automatically: it has 1 unresolved ' +
      'CodeRabbit major thread(s).',
    '',
    'That is only a note about the automatic merge.',
  ].join('\n'),
});

const plain = (body = 'Looks fine to me.') => ({ author: { login: 'coderabbitai' }, body });
const major = (body = '**Major** — the lock is taken after the read.') =>
  ({ author: { login: 'coderabbitai' }, body });

// Answers the workflow's GraphQL query from a fixture, honouring the page size
// it asks for: `comments(first: N)` returns N nodes and says whether more
// remain, exactly as the API would.
function answerThreads(query, threads) {
  const asked = query.match(/comments\(first:\s*(\d+)\)/);
  assert.ok(asked, 'the workflow asks for the thread comments');
  const first = Number(asked[1]);
  const page = (all) => ({
    pageInfo: { hasNextPage: all.length > first },
    nodes: all.slice(0, first),
  });
  return {
    repository: {
      pullRequest: {
        reviewThreads: {
          pageInfo: { hasNextPage: false },
          nodes: threads.map((thread) => ({
            isResolved: Boolean(thread.resolved),
            isOutdated: false,
            comments: page(thread.comments),
          })),
        },
      },
    },
  };
}

// Everything the script needs from GitHub, for one pull request that would
// merge if no gate refused it: open, `risk: low`, a documentation file only,
// an approving verdict naming the head, the three required checks green, and a
// CodeRabbit review of that same head.
async function evaluate({
  threads, comments, reviews, checks, order, body = 'Reviewed, green on this head.',
  commentsError = null, files, author, headRepo, commits, commitCount,
}) {
  const log = [];
  const merged = [];
  const posted = [];
  const deleted = [];
  // How many times the comment list was read: a transient failure is the first
  // call and only it, so a second read (if anything makes one) succeeds.
  let commentReads = 0;
  // A file carries its new name, and `previous_filename` when it was renamed.
  const changedFiles = files || [{ filename: 'docs/notes.md' }];
  const prCommits = commits || [];
  const state = {
    pr: {
      number: 42,
      state: 'open',
      draft: false,
      base: { ref: 'main' },
      head: { sha: HEAD, repo: { full_name: headRepo || 'securo-finance/securo' } },
      user: author || { login: 'securo-dev', type: 'User' },
      commits: commitCount ?? prCommits.length,
      title: 'docs: keep the notes current',
      body,
      labels: [{ name: 'risk: low' }],
      changed_files: changedFiles.length,
    },
    files: changedFiles,
    reviews: reviews || [coderabbitReview()],
    checks: checks || GREEN,
    comments: comments || [{
      user: { login: 'tassionoronha' },
      body: VERDICT,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    }],
  };
  const github = {
    paginate: async (fn, params) => fn(params),
    graphql: async (query) => {
      if (order) order.push('threads');
      return answerThreads(query, threads);
    },
    rest: {
      pulls: {
        get: async () => ({ data: state.pr }),
        list: async () => [],
        listFiles: async () => state.files,
        listReviews: async () => state.reviews,
        listCommits: async () => prCommits,
        merge: async (params) => { merged.push(params); return { data: {} }; },
      },
      issues: {
        listComments: async () => {
          commentReads += 1;
          if (commentsError && commentReads === 1) throw new Error(commentsError);
          return state.comments;
        },
        createComment: async (params) => { posted.push(params); return { data: {} }; },
        updateComment: async () => ({ data: {} }),
        deleteComment: async (params) => {
          deleted.push(params.comment_id);
          return { data: {} };
        },
      },
      checks: {
        listForRef: async () => {
          if (order) order.push('checks');
          return { data: { check_runs: state.checks } };
        },
      },
      actions: {
        createWorkflowDispatch: async () => ({ data: {} }),
        listWorkflowRuns: async () => ({ data: { workflow_runs: [] } }),
      },
    },
  };
  const context = {
    eventName: 'workflow_dispatch',
    repo: { owner: 'securo-finance', repo: 'securo' },
    payload: { inputs: { pr: '42' } },
  };
  const core = {
    info: (message) => log.push(message),
    warning: (message) => log.push(message),
    error: (message) => log.push(message),
  };
  const run = new Function('github', 'context', 'core',
    `return (async () => {\n${readScript()}\n})();`);
  await run(github, context, core);
  return {
    merged: merged.length > 0,
    mergeParams: merged[0] || null,
    log: log.join('\n'),
    posted,
    deleted,
  };
}

const scenarios = [];
const scenario = (name, fn) => scenarios.push({ name, fn });

// The bug of #1596: five comments was the page, a thread that collects a few
// CodeRabbit back-and-forths passes it, and the lane then refused the pull
// request as unreadable for good. Six comments and nothing to hold it has to
// merge.
scenario('a thread of six comments with no finding still merges', async () => {
  const { merged, log } = await evaluate({
    threads: [{ comments: [plain(), plain(), plain(), plain(), plain(), plain()] }],
  });
  assert.ok(merged, `expected a merge, got: ${log}`);
});

// The severity marker is in the thread's first comment. Reading the page whole
// must decide on it, with other threads around it, instead of calling the
// thread unreadable.
scenario('three threads, one with six comments and an open Major, refuse on the Major',
  async () => {
    const { merged, log } = await evaluate({
      threads: [
        { comments: [plain('Nothing to change here.')] },
        { comments: [major(), plain(), plain(), plain(), plain(), plain()] },
        { comments: [plain()], resolved: true },
      ],
    });
    assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
    assert.match(log, /1 unresolved CodeRabbit major thread\(s\)/,
      `the Major has to be the reason: ${log}`);
    assert.doesNotMatch(log, /more comments than this check can read/,
      `the thread is readable now and must not be called unreadable: ${log}`);
  });

// Past a full page the gate cannot see the whole thread, and a blocker could
// hide in the part it did not read: it keeps refusing.
scenario('a thread past a full page keeps refusing', async () => {
  const comments = Array.from({ length: 101 }, () => plain());
  const { merged, log } = await evaluate({ threads: [{ comments }] });
  assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
  assert.match(log, /more comments than this check can read/, log);
});

// A resolved thread is triaged whatever it says, and its length does not
// matter either.
scenario('a resolved thread of six comments with a Major merges', async () => {
  const { merged, log } = await evaluate({
    threads: [{ comments: [major(), plain(), plain(), plain(), plain(), plain()], resolved: true }],
  });
  assert.ok(merged, `expected a merge, got: ${log}`);
});

// The bug of #183: an empty thread list is what a pull request looks like both
// after a clean review and after one the bot never opened. With no review on
// this head, "no open thread" says nothing, so the lane refuses — and says it
// on the pull request, where the head already carries an approval (#1553).
scenario('a head the CodeRabbit never reviewed refuses, and the pull request says why',
  async () => {
    const { merged, log, posted } = await evaluate({ threads: [], reviews: [] });
    assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
    assert.match(log, /CodeRabbit has no review of this commit/, log);
    assert.equal(posted.length, 1, `the refusal belongs in one comment: ${log}`);
    assert.match(posted[0].body, /CodeRabbit has no review of this commit/, posted[0].body);
  });

// #1585: the note describes the head it was written for, and a verdict names
// one commit. An approved pull request was refused, the author pushed a fix,
// and no verdict names the new head any more — the note is then about a state
// the current commit no longer has, so it goes instead of being kept.
scenario('a new head with no verdict clears the note written for the old one',
  async () => {
    const { merged, log, posted, deleted } = await evaluate({
      threads: [{ comments: [plain()] }],
      comments: [{
        user: { login: 'tassionoronha' },
        body: STALE_VERDICT,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      }, refusalNote()],
    });
    assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
    assert.match(log, /carries no approval from an identity other than its author/, log);
    assert.deepEqual(deleted, [9001],
      `the note written for the previous head has to go: ${log}`);
    assert.equal(posted.length, 0,
      `an unapproved head gets no note of its own: ${log}`);
  });

// The other half: no note may be deleted on a read that failed. An API error
// says nothing about the head, and a transient hiccup must not take away the
// only place the refusal is written down. The failure is the first read only,
// and the note is there for any second one, so a fix that retries the list
// after the error deletes it and fails this scenario.
scenario('a failed comment read keeps the refusal note', async () => {
  const { merged, log, posted, deleted } = await evaluate({
    threads: [{ comments: [plain()] }],
    comments: [refusalNote()],
    commentsError: 'HTTP 502',
  });
  assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
  assert.match(log, /could not read the comments: HTTP 502/, log);
  assert.deepEqual(deleted, [], `a failed read deletes nothing: ${log}`);
  assert.equal(posted.length, 0, log);
});

// The case that actually happened: the bot was rate limited on this head. Its
// check run is `completed` and `neutral`, so a gate reading the conclusion
// would call it done; only the title says no review was made (`#1553`).
scenario('a head the CodeRabbit skipped as rate limited refuses, and names it', async () => {
  const { merged, log } = await evaluate({
    threads: [],
    reviews: [coderabbitReview(OLD_HEAD)],
    checks: [...GREEN, coderabbitCheck('neutral', 'Review rate limited')],
  });
  assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
  assert.match(log,
    /CodeRabbit has no review of this commit: its check says "Review rate limited"/, log);
});

// The same hole with the other title it uses: a paused review ends `skipped`,
// and the review it names is the previous head's (`#1568`).
scenario('a head with its review paused refuses too', async () => {
  const { merged, log } = await evaluate({
    threads: [],
    reviews: [coderabbitReview(OLD_HEAD)],
    checks: [...GREEN, coderabbitCheck('skipped', 'Review paused')],
  });
  assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
  assert.match(log, /"Review paused"/, log);
});

// The happy case: CodeRabbit did review this head, so the lane still merges
// without anybody pressing a button.
scenario('a head the CodeRabbit reviewed merges', async () => {
  const { merged, log } = await evaluate({
    threads: [{ comments: [plain()] }],
    checks: [...GREEN, coderabbitCheck('success', 'Review completed')],
  });
  assert.ok(merged, `expected a merge, got: ${log}`);
  assert.doesNotMatch(log, /CodeRabbit has no review/, log);
});

// A stale check run from an earlier attempt must not outweigh a review that
// does name this head: the review is the evidence, the check is only the
// explanation for its absence.
scenario('a review on the head wins over a stale rate-limit check', async () => {
  const { merged, log } = await evaluate({
    threads: [],
    checks: [...GREEN, coderabbitCheck('neutral', 'Review rate limited')],
  });
  assert.ok(merged, `expected a merge, got: ${log}`);
});

// #1612: on an incremental push that leaves nothing to say, CodeRabbit
// finishes the review without submitting a review object. Its check run is
// bound to the head and says "Review completed", which is evidence of a review
// of this exact sha, so the lane merges instead of refusing it forever.
scenario('a head whose check completed the review, with no review object, merges',
  async () => {
    const { merged, log } = await evaluate({
      threads: [],
      reviews: [coderabbitReview(OLD_HEAD)],
      checks: [...GREEN, coderabbitCheck('success', 'Finished',
        'Result: Success - Review completed')],
    });
    assert.ok(merged, `expected a merge, got: ${log}`);
    assert.doesNotMatch(log, /CodeRabbit has no review/, log);
  });

// The check run has to be read before the thread list. A review that completes
// between the two has findings the list that came first does not carry, and
// the lane would merge a Major it never saw — the ordering CodeRabbit called
// out on #1605. Reading the evidence first means everything a completed review
// published is in the list that follows it.
scenario('the check run is read before the review threads', async () => {
  const order = [];
  const { merged, log } = await evaluate({
    threads: [],
    reviews: [coderabbitReview(OLD_HEAD)],
    checks: [...GREEN, coderabbitCheck('success', 'Finished',
      'Result: Success - Review completed')],
    order,
  });
  assert.ok(merged, `expected a merge, got: ${log}`);
  const checksAt = order.indexOf('checks');
  const threadsAt = order.indexOf('threads');
  assert.ok(checksAt !== -1 && threadsAt !== -1, `both were read: ${order}`);
  assert.ok(checksAt < threadsAt, `the check run is read first: ${order}`);
});

// A review naming the head is evidence on its own, so the check run is not
// read for it: the one read left is gate 5's, after the thread list.
scenario('a review on the head does not spend a check read', async () => {
  const order = [];
  const { merged, log } = await evaluate({ threads: [], order });
  assert.ok(merged, `expected a merge, got: ${log}`);
  assert.deepEqual(order, ['threads', 'checks'], `only gate 5 reads it: ${order}`);
});

// `success` on its own is not a review: a check that never says the review was
// completed must not be read as one, or the gate loosens to "any green run".
scenario('a successful check that does not say it reviewed refuses', async () => {
  const { merged, log } = await evaluate({
    threads: [],
    reviews: [coderabbitReview(OLD_HEAD)],
    checks: [...GREEN, coderabbitCheck('success', 'Finished', 'Result: Success')],
  });
  assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
  assert.match(log, /CodeRabbit has no review of this commit/, log);
});

// The bug of #1609: GitHub builds the squash message from the pull request's
// title and the messages of its commits, and the description — where this
// repository writes "Closes #N" — is not part of it. The commit landed without
// a keyword and the issue stayed open (#1356 from #1568). The closing lines of
// the description have to reach the merge call.
scenario('the closing keywords of the description reach the squash commit',
  async () => {
    const { merged, mergeParams, log } = await evaluate({
      threads: [],
      body: '## Summary\n\nSmall fix.\n\nCloses #1356\nFixes: #1357\n',
    });
    assert.ok(merged, `expected a merge, got: ${log}`);
    assert.equal(mergeParams.commit_message, 'Closes #1356\nFixes: #1357',
      'only the closing lines go into the commit, and both of them do');
    assert.match(mergeParams.commit_title, /\(#42\)$/,
      'the title keeps the pull request number GitHub appends by default');
  });

// No keyword in the description means there is nothing to carry: the call
// stays as it was and GitHub builds its default message, which already covers
// a keyword written in a commit. Neither field may be sent: passing either one
// replaces that message instead of appending to it.
scenario('a description with no closing keyword leaves the default message',
  async () => {
    const { merged, mergeParams, log } = await evaluate({
      threads: [],
      body: '## Summary\n\nA cleanup with nothing to close.',
    });
    assert.ok(merged, `expected a merge, got: ${log}`);
    assert.equal(mergeParams.commit_title, undefined,
      'the title is not sent alone: it would replace the default message');
    assert.equal(mergeParams.commit_message, undefined,
      'no closing keyword means GitHub builds the message as before');
  });

// The bug of #221: the lane knew sync and money only by the file name, and
// the engines are not named that way. #1625 changed the sync engine in three
// of these files and the lane still called it free to merge. Each one is a
// separate case, so a path dropped from the rule shows up here by name.
const ENGINE_FILES = [
  'backend/app/services/connection_service.py',        // sync
  'backend/app/services/card_mirror_service.py',       // card copy set aside during sync
  'backend/app/services/fx_rate_service.py',           // exchange rates
  'backend/app/services/reconciliation_service.py',    // pairs and history
  'backend/app/services/reconciliation_engine.py',     // the pure matcher
  'backend/app/services/reconciliation_policy.py',     // who may match what
  'backend/app/services/_query_filters.py',            // what counts as income
  'backend/app/services/loan_schedule.py',             // amortization
  'backend/app/services/transfer_detection_service.py',// which lines become transfers
  'backend/app/services/transaction_match_service.py', // new line or duplicate
  'backend/app/services/credit_card_service.py',       // which bill a purchase falls in
  'backend/app/services/provider_settings.py',         // bank provider configuration
];

for (const file of ENGINE_FILES) {
  scenario(`a pull request that edits ${file.split('/').pop()} refuses`, async () => {
    const { merged, log } = await evaluate({ threads: [], files: [{ filename: file }] });
    assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
    assert.match(log,
      new RegExp(`\`${file.replace(/[.]/g, '\\.')}\` is in a part of the repository that is ` +
        'never merged automatically \\(money or bank sync\\)'), log);
  });
}

// The list #1625 actually carried: the sync engine, the card mirror and the
// reconciliation policy, beside a page and a test. No file name matched, so
// the lane called it free to merge — a person was what held it.
scenario('the file list of #1625 refuses as money or bank sync', async () => {
  const { merged, log } = await evaluate({
    threads: [],
    files: [
      { filename: 'AGENTS.md' },
      { filename: 'backend/app/services/card_mirror_service.py' },
      { filename: 'backend/app/services/connection_service.py' },
      { filename: 'backend/app/services/reconciliation_policy.py' },
      { filename: 'backend/tests/test_card_mirror.py' },
    ],
  });
  assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
  assert.ok(log.includes('card_mirror_service.py` is in a part of the repository that is ' +
    'never merged automatically (money or bank sync)'), log);
});

// Renaming a blocked file into a free path must not hide it: the previous name
// is read as well as the new one.
scenario('one of those files renamed out of the directory still refuses', async () => {
  const { merged, log } = await evaluate({
    threads: [],
    files: [{
      filename: 'backend/app/helpers/sync_engine.py',
      previous_filename: 'backend/app/services/connection_service.py',
    }],
  });
  assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
  assert.match(log, /\(money or bank sync\)/, log);
});

// Every other service keeps the verdict it had: the rule names files, so a
// helper that is not one of them is still free to merge.
scenario('a service the rule does not name still merges', async () => {
  const { merged, log } = await evaluate({
    threads: [],
    files: [{ filename: 'backend/app/services/payee_service.py' }],
  });
  assert.ok(merged, `expected a merge, got: ${log}`);
});

// The four services kept out of the rule on purpose. Blocking them would take
// most pull requests out of the lane, and none runs a sync or decides a
// balance the way the named engines do.
for (const file of [
  'backend/app/services/transaction_service.py',
  'backend/app/services/account_service.py',
  'backend/app/services/import_service.py',
  'backend/app/services/backup_service.py',
]) {
  scenario(`${file.split('/').pop()} stays free, as it was`, async () => {
    const { merged, log } = await evaluate({ threads: [], files: [{ filename: file }] });
    assert.ok(merged, `expected a merge, got: ${log}`);
  });
}

// The tail accepts a directory of that name: an engine moved into a package is
// the same engine, and its `__init__.py` would otherwise read as an ordinary
// service.
scenario('a package named for an engine refuses', async () => {
  const { merged, log } = await evaluate({
    threads: [],
    files: [{ filename: 'backend/app/services/connection_service/__init__.py' }],
  });
  assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
  assert.match(log, /\(money or bank sync\)/, log);
});

// The one wildcard in the group takes digits too, so a versioned
// reconciliation engine is not a way around it.
scenario('a versioned reconciliation engine refuses', async () => {
  const { merged, log } = await evaluate({
    threads: [],
    files: [{ filename: 'backend/app/services/reconciliation_v2.py' }],
  });
  assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
  assert.match(log, /\(money or bank sync\)/, log);
});

// Documentation never matches a blocked rule, whatever it sits beside: a page
// under docs/ and a chart's README describe the engines, they do not run them.
scenario('documentation about those engines still merges', async () => {
  const { merged, log } = await evaluate({
    threads: [],
    files: [
      { filename: 'docs/adr/0027-card-mirror.md' },
      { filename: 'charts/securo/README.md' },
      { filename: 'backend/app/services/README.md' },
    ],
  });
  assert.ok(merged, `expected a merge, got: ${log}`);
});

// The rules that were already there keep the verdict they had: each group
// still refuses its own paths, with its own reason, and code outside every
// group is still free. This is what says the new rule adds a gate instead of
// replacing one.
const EXISTING_BLOCKED = [
  ['backend/app/core/auth.py', 'auth or secrets'],
  ['backend/app/api/custom_auth.py', 'auth API'],
  ['.github/workflows/ci.yml', 'CI or repository configuration'],
  ['backend/alembic/versions/027_money.py', 'schema migration'],
  ['backend/app/providers/pluggy.py', 'bank provider'],
  ['charts/securo/Chart.yaml', 'deployment'],
];

for (const [file, what] of EXISTING_BLOCKED) {
  scenario(`${file} keeps refusing as ${what}`, async () => {
    const { merged, log } = await evaluate({ threads: [], files: [{ filename: file }] });
    assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
    assert.ok(log.includes(`(${what})`), `expected the reason "${what}": ${log}`);
  });
}

// The rule the new one sits beside still catches what it always did, and says
// it in its own words: a service named for a sync is refused as `sync or
// plugin`, not as the new group.
scenario('a service named for a sync still refuses as sync or plugin', async () => {
  const { merged, log } = await evaluate({
    threads: [],
    files: [{ filename: 'backend/app/services/connection_sync.py' }],
  });
  assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
  assert.ok(log.includes('(sync or plugin)'), `expected the old reason: ${log}`);
});

scenario('a frontend page outside every group still merges', async () => {
  const { merged, log } = await evaluate({
    threads: [],
    files: [{ filename: 'frontend/src/pages/transactions.tsx' }],
  });
  assert.ok(merged, `expected a merge, got: ${log}`);
});

// CodeRabbit skips pull requests opened by a bot ("Review skipped: author
// ignored by configuration"), so gate 3c refused every Renovate bump for a
// review that never comes (#1763). A bump only the bot wrote waives that one
// gate; anything a person added keeps it.
const RENOVATE = { login: 'renovate[bot]', type: 'Bot' };
const botCommit = (sha = HEAD, overrides = {}) => ({
  sha,
  author: { login: 'renovate[bot]' },
  committer: { login: 'web-flow' },
  commit: { verification: { verified: true } },
  ...overrides,
});
const SKIPPED = coderabbitCheck('neutral', 'Review skipped',
  'Review skipped: author ignored by configuration');
const BUMP_FILES = [
  { filename: 'frontend/package.json' },
  { filename: 'frontend/package-lock.json' },
];
const renovateBump = (overrides = {}) => evaluate({
  threads: [],
  reviews: [],
  checks: [...GREEN, SKIPPED],
  author: RENOVATE,
  files: BUMP_FILES,
  commits: [botCommit()],
  ...overrides,
});

scenario('a Renovate bump only the bot wrote merges without a CodeRabbit review',
  async () => {
    const { merged, log } = await renovateBump();
    assert.ok(merged, `expected a merge, got: ${log}`);
    assert.match(log, /CodeRabbit skips its author, so its review is not required/, log);
  });

scenario('a Python lock bump only the bot wrote merges too', async () => {
  const { merged, log } = await renovateBump({
    files: [{ filename: 'backend/pyproject.toml' }, { filename: 'backend/uv.lock' }],
  });
  assert.ok(merged, `expected a merge, got: ${log}`);
});

// #1763: a person stacked a fix on the Renovate branch. That code was never
// read by CodeRabbit, so the gate stands.
scenario('a person\'s commit on a Renovate branch keeps the CodeRabbit gate', async () => {
  const { merged, log } = await renovateBump({
    files: [...BUMP_FILES, { filename: 'frontend/src/pages/dashboard.tsx' }],
    commits: [botCommit(), botCommit(OLD_HEAD, { author: { login: 'tassionoronha' } })],
  });
  assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
  assert.match(log, /CodeRabbit has no review of this commit/, log);
});

scenario('a Renovate pull request touching a source file keeps the gate', async () => {
  const { merged, log } = await renovateBump({
    files: [...BUMP_FILES, { filename: 'frontend/src/pages/dashboard.tsx' }],
  });
  assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
  assert.match(log, /not waived for this bot pull request: it changes frontend\/src/, log);
});

scenario('renovate.json in a bot pull request keeps the gate', async () => {
  const { merged, log } = await renovateBump({
    files: [...BUMP_FILES, { filename: 'renovate.json' }],
  });
  assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
  assert.match(log, /it changes renovate\.json/, log);
});

// A local commit can carry any author name; only a commit GitHub signed for
// the bot is the bot's.
scenario('an unsigned commit claiming to be the bot keeps the gate', async () => {
  const { merged, log } = await renovateBump({
    commits: [botCommit(HEAD, {
      committer: { login: 'tassionoronha' },
      commit: { verification: { verified: false } },
    })],
  });
  assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
  assert.match(log, /is not a signed commit of renovate\[bot\]/, log);
});

scenario('a bot pull request from a fork keeps the gate', async () => {
  const { merged, log } = await renovateBump({ headRepo: 'someone/securo' });
  assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
  assert.match(log, /its branch is not in this repository/, log);
});

// The login alone is not enough: a user account cannot be a bot.
scenario('a user named like the bot keeps the gate', async () => {
  const { merged, log } = await renovateBump({
    author: { login: 'renovate[bot]', type: 'User' },
  });
  assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
  assert.match(log, /CodeRabbit has no review of this commit/, log);
});

// The waiver is about CodeRabbit only: every other gate still holds.
scenario('a waived bump still needs a verdict naming the head', async () => {
  const { merged, log } = await renovateBump({ comments: [] });
  assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
  assert.match(log, /carries no approval/, log);
});

scenario('a waived bump still needs the required checks green', async () => {
  const red = GREEN.map((c) => (c.name === 'Backend Tests'
    ? { ...c, conclusion: 'failure' } : c));
  const { merged, log } = await renovateBump({ checks: [...red, SKIPPED] });
  assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
  assert.match(log, /Backend Tests check ended as failure/, log);
});

scenario('a waived bump still refuses on a blocked path', async () => {
  const { merged, log } = await renovateBump({
    files: [{ filename: 'backend/Dockerfile' }],
  });
  assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
  assert.match(log, /\(deployment\)/, log);
});

scenario('a waived bump still refuses on an open Major thread', async () => {
  const { merged, log } = await renovateBump({ threads: [{ comments: [major()] }] });
  assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
  assert.match(log, /unresolved CodeRabbit major thread/, log);
});

// Commits past what the list returned could be anyone's, so the waiver fails
// closed, the same way the file gate does.
scenario('a bot pull request whose commits were not all read keeps the gate', async () => {
  const { merged, log } = await renovateBump({ commitCount: 300 });
  assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
  assert.match(log, /only 1 of its 300 commits could be read/, log);
});

scenario('a bot pull request with no commit read keeps the gate', async () => {
  const { merged, log } = await renovateBump({ commits: [] });
  assert.ok(!merged, `expected a refusal, got a merge: ${log}`);
  assert.match(log, /no commit to read/, log);
});

let failed = 0;
for (const { name, fn } of scenarios) {
  try {
    await fn();
    console.log(`ok - ${name}`);
  } catch (error) {
    failed += 1;
    console.error(`not ok - ${name}\n  ${error.message}`);
  }
}
if (failed) {
  console.error(`\n${failed} of ${scenarios.length} scenarios failed`);
  process.exit(1);
}
console.log(`\nall ${scenarios.length} scenarios passed`);
