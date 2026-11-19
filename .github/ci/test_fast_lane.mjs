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
}) {
  const log = [];
  const merged = [];
  const posted = [];
  const state = {
    pr: {
      state: 'open',
      draft: false,
      base: { ref: 'main' },
      head: { sha: HEAD },
      user: { login: 'securo-dev' },
      title: 'docs: keep the notes current',
      body,
      labels: [{ name: 'risk: low' }],
      changed_files: 1,
    },
    files: [{ filename: 'docs/notes.md' }],
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
        merge: async (params) => { merged.push(params); return { data: {} }; },
      },
      issues: {
        listComments: async () => state.comments,
        createComment: async (params) => { posted.push(params); return { data: {} }; },
        updateComment: async () => ({ data: {} }),
        deleteComment: async () => ({ data: {} }),
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
