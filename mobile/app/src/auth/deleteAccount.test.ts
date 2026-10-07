import { describe, it, expect } from '@jest/globals';

import { deleteAccountErrorTr, deleteAccountInOrder, type DeleteAccountSteps } from './deleteAccount';

/** Steps that record the order they ran in, failing the named one. */
function recordingSteps(fail?: { step: keyof DeleteAccountSteps; error: unknown }) {
  const calls: string[] = [];
  const run = (name: keyof DeleteAccountSteps, label: string) => async () => {
    calls.push(label);
    if (fail?.step === name) throw fail.error;
  };
  const steps: DeleteAccountSteps = {
    reauthenticate: async (password) => run('reauthenticate', `reauth:${password}`)(),
    deleteServerData: run('deleteServerData', 'server'),
    deleteIdentity: run('deleteIdentity', 'identity'),
  };
  return { calls, steps };
}

describe('account deletion order', () => {
  it('proves the password, then deletes the server data, then the identity', async () => {
    const { calls, steps } = recordingSteps();
    await expect(deleteAccountInOrder('hunter22', steps)).resolves.toEqual({ ok: true });
    expect(calls).toEqual(['reauth:hunter22', 'server', 'identity']);
  });

  it('deletes nothing when re-authentication fails', async () => {
    // The bug this exists for: a stale session made Firebase refuse
    // deleteUser AFTER the backend had already deleted everything.
    const { calls, steps } = recordingSteps({
      step: 'reauthenticate',
      error: { code: 'auth/invalid-credential' },
    });
    const outcome = await deleteAccountInOrder('wrong', steps);
    expect(outcome).toMatchObject({ ok: false, stage: 'reauth' });
    expect(calls).toEqual(['reauth:wrong']);
  });

  it('keeps the identity when the server data could not be deleted', async () => {
    const { calls, steps } = recordingSteps({
      step: 'deleteServerData',
      error: { response: { status: 500 } },
    });
    const outcome = await deleteAccountInOrder('pw', steps);
    expect(outcome).toMatchObject({ ok: false, stage: 'server' });
    expect(calls).toEqual(['reauth:pw', 'server']);
  });

  it('reports an identity failure as the half-done state it is', async () => {
    const { steps } = recordingSteps({
      step: 'deleteIdentity',
      error: { code: 'auth/network-request-failed' },
    });
    const outcome = await deleteAccountInOrder('pw', steps);
    expect(outcome).toMatchObject({ ok: false, stage: 'identity' });
    if (outcome.ok) throw new Error('unreachable');
    expect(outcome.message).toContain('Verilerin silindi');
    expect(outcome.message).toContain('tekrar dene');
  });
});

describe('re-authentication errors', () => {
  it('says the password was wrong — there is no account to enumerate here', () => {
    expect(deleteAccountErrorTr('reauth', { code: 'auth/invalid-credential' })).toContain('Şifre hatalı');
    expect(deleteAccountErrorTr('reauth', { code: 'auth/wrong-password' })).toBe(
      deleteAccountErrorTr('reauth', { code: 'auth/invalid-credential' }),
    );
  });

  it('every failure before the server call says nothing was deleted', () => {
    for (const code of [
      'auth/invalid-credential',
      'auth/too-many-requests',
      'auth/network-request-failed',
      'auth/some-future-code',
    ]) {
      expect(deleteAccountErrorTr('reauth', { code })).toContain('silinmedi');
    }
  });

  it('a missing session sends the user to sign in again rather than retry', () => {
    for (const code of ['auth/user-signed-out', 'auth/user-token-expired', 'auth/user-not-found']) {
      expect(deleteAccountErrorTr('reauth', { code })).toContain('yeniden giriş');
    }
  });

  it('rate limiting tells the user to wait', () => {
    expect(deleteAccountErrorTr('reauth', { code: 'auth/too-many-requests' })).toContain('bekle');
  });
});

describe('server errors', () => {
  it('tells an unreachable server apart from a refusal', () => {
    const offline = deleteAccountErrorTr('server', new TypeError('Network request failed'));
    const refused = deleteAccountErrorTr('server', { response: { status: 500 } });
    expect(offline).toContain('ulaşılamadı');
    expect(refused).not.toBe(offline);
    expect(refused).toContain('silinmedi');
  });
});

describe('identity errors', () => {
  it('a stale sign-in asks for the password again', () => {
    expect(deleteAccountErrorTr('identity', { code: 'auth/requires-recent-login' })).toContain('Şifreni');
  });

  it.each(['auth/requires-recent-login', 'auth/network-request-failed', 'auth/internal-error'])(
    '%s says the data is already gone',
    (code) => {
      expect(deleteAccountErrorTr('identity', { code })).toContain('Verilerin silindi');
    },
  );
});

describe('every message is sayable', () => {
  const stages = ['reauth', 'server', 'identity'] as const;

  it('never echoes the raw Firebase code', () => {
    for (const stage of stages) {
      for (const code of ['auth/invalid-credential', 'auth/requires-recent-login', 'auth/some-future-code']) {
        expect(deleteAccountErrorTr(stage, { code })).not.toContain('auth/');
      }
    }
  });

  it.each([null, undefined, 'a string', 42, {}, { code: null }, new Error('boom')])(
    'handles %p without throwing',
    (value) => {
      for (const stage of stages) {
        expect(deleteAccountErrorTr(stage, value).length).toBeGreaterThan(0);
      }
    },
  );
});
