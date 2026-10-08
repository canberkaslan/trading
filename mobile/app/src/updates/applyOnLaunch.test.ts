import { describe, it, expect, jest } from '@jest/globals';

import { applyUpdateOnLaunch, RELOAD_WINDOW_MS, type UpdatesApi } from './applyOnLaunch';

function fakeApi(over: Partial<UpdatesApi> = {}) {
  const api = {
    isEnabled: true,
    checkForUpdateAsync: jest.fn(async () => ({ isAvailable: true })),
    fetchUpdateAsync: jest.fn(async () => ({ isNew: true })),
    reloadAsync: jest.fn(async () => {}),
    ...over,
  };
  return api;
}

describe('applying an OTA on the launch that finds it', () => {
  it('reloads into a new update found right after launch', async () => {
    const api = fakeApi();
    expect(await applyUpdateOnLaunch(api, () => 0)).toBe('reloaded');
    expect(api.reloadAsync).toHaveBeenCalledTimes(1);
  });

  it('does nothing when expo-updates is off (dev client, web)', async () => {
    const api = fakeApi({ isEnabled: false });
    expect(await applyUpdateOnLaunch(api)).toBe('disabled');
    expect(api.checkForUpdateAsync).not.toHaveBeenCalled();
  });

  it('does not reload when the running bundle is already the latest', async () => {
    const api = fakeApi({ checkForUpdateAsync: jest.fn(async () => ({ isAvailable: false })) });
    expect(await applyUpdateOnLaunch(api)).toBe('current');
    expect(api.fetchUpdateAsync).not.toHaveBeenCalled();
    expect(api.reloadAsync).not.toHaveBeenCalled();
  });

  it('does not reload into the update it is already running — no reload loop', async () => {
    const api = fakeApi({ fetchUpdateAsync: jest.fn(async () => ({ isNew: false })) });
    expect(await applyUpdateOnLaunch(api)).toBe('current');
    expect(api.reloadAsync).not.toHaveBeenCalled();
  });

  it('leaves a slow download for the next launch instead of yanking the screen', async () => {
    const times = [0, RELOAD_WINDOW_MS + 1];
    const api = fakeApi();
    expect(await applyUpdateOnLaunch(api, () => times.shift() ?? 0)).toBe('next-launch');
    expect(api.reloadAsync).not.toHaveBeenCalled();
  });

  it.each(['checkForUpdateAsync', 'fetchUpdateAsync', 'reloadAsync'] as const)(
    'never throws when %s fails',
    async (step) => {
      const api = fakeApi({
        [step]: jest.fn(async () => {
          throw new Error('offline');
        }),
      });
      expect(await applyUpdateOnLaunch(api, () => 0)).toBe('failed');
    },
  );
});
