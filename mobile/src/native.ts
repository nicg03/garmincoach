import { App } from '@capacitor/app';
import { Browser } from '@capacitor/browser';
import { Capacitor } from '@capacitor/core';
import { Keyboard } from '@capacitor/keyboard';
import { LocalNotifications } from '@capacitor/local-notifications';
import { Network } from '@capacitor/network';
import { SplashScreen } from '@capacitor/splash-screen';
import { StatusBar, Style } from '@capacitor/status-bar';
import { KeychainAccess, SecureStorage } from '@aparajita/capacitor-secure-storage';

const API_BASE = 'https://gepard.fit';
const SESSION_KEY = 'mobile-session';
const REMINDER_KEY = 'daily-reminder';
const REMINDER_ID = 1001;

type Reminder = { enabled: boolean; hour: number; minute: number };

document.documentElement.classList.add('native-app');

async function initialise() {
  await SecureStorage.setKeyPrefix('gepard.fit.');
  if (Capacitor.getPlatform() === 'ios') {
    await SecureStorage.setDefaultKeychainAccess(KeychainAccess.whenUnlockedThisDeviceOnly);
  }
  await StatusBar.setStyle({ style: Style.Dark }).catch(() => {});
  await SplashScreen.hide().catch(() => {});
  const status = await Network.getStatus();
  setOnline(status.connected);
  const launch = await App.getLaunchUrl();
  if (launch?.url) routeFromUrl(launch.url);
}

function setOnline(connected: boolean) {
  document.documentElement.classList.toggle('is-offline', !connected);
  window.dispatchEvent(new CustomEvent('gepard:network', { detail: { connected } }));
}

async function getToken(): Promise<string | null> {
  const value = await SecureStorage.get(SESSION_KEY, false);
  return typeof value === 'string' ? value : null;
}

async function setToken(token: string): Promise<void> {
  await SecureStorage.set(SESSION_KEY, token, false);
}

async function clearToken(): Promise<void> {
  await SecureStorage.remove(SESSION_KEY);
}

async function getReminder(): Promise<Reminder> {
  const value = await SecureStorage.get(REMINDER_KEY, false);
  if (value && typeof value === 'object' && 'hour' in value && 'minute' in value) {
    const saved = value as Record<string, unknown>;
    return {
      enabled: Boolean(saved.enabled),
      hour: Number(saved.hour),
      minute: Number(saved.minute),
    };
  }
  return { enabled: false, hour: 7, minute: 30 };
}

async function setReminder(reminder: Reminder): Promise<boolean> {
  await LocalNotifications.cancel({ notifications: [{ id: REMINDER_ID }] });
  if (reminder.enabled) {
    const permission = await LocalNotifications.requestPermissions();
    if (permission.display !== 'granted') return false;
    await LocalNotifications.schedule({
      notifications: [{
        id: REMINDER_ID,
        title: 'gepard.fit',
        body: 'Your readiness and today’s training are ready.',
        schedule: {
          on: { hour: reminder.hour, minute: reminder.minute },
          repeats: true,
          allowWhileIdle: true,
        },
        extra: { route: 'today' },
      }],
    });
  }
  await SecureStorage.set(REMINDER_KEY, reminder, false);
  return true;
}

function routeFromUrl(raw: string) {
  try {
    const url = new URL(raw);
    if (url.hostname !== 'gepard.fit') return;
    if (url.hash.startsWith('#/')) {
      location.hash = url.hash;
    }
  } catch {
    // Ignore malformed URLs delivered by another application.
  }
}

const ready = initialise().catch((error) => {
  console.error('[gepard.fit] native bridge failed', error);
});

window.addEventListener('DOMContentLoaded', () => {
  const banner = document.createElement('div');
  banner.className = 'native-offline';
  banner.textContent = 'No network connection';
  banner.setAttribute('role', 'status');
  document.body.append(banner);

  document.addEventListener('click', (event) => {
    const anchor = (event.target as HTMLElement).closest('a[href]') as HTMLAnchorElement | null;
    if (!anchor) return;
    const raw = anchor.getAttribute('href') || '';
    if (raw.startsWith('#')) return;
    if (raw === '/privacy' || raw === '/support') {
      event.preventDefault();
      Browser.open({ url: API_BASE + raw });
      return;
    }
    const url = new URL(raw, location.href);
    if (url.protocol === 'http:' || url.protocol === 'https:') {
      if (/^https?:\/\//.test(raw) && url.hostname !== 'gepard.fit') {
        event.preventDefault();
        Browser.open({ url: url.href });
      }
    }
  });
});

Network.addListener('networkStatusChange', ({ connected }) => setOnline(connected));
App.addListener('appUrlOpen', ({ url }) => routeFromUrl(url));
App.addListener('backButton', ({ canGoBack }) => {
  if (canGoBack && location.hash && location.hash !== '#/today') history.back();
  else App.exitApp();
});
LocalNotifications.addListener('localNotificationActionPerformed', () => {
  location.hash = '#/today';
});
Keyboard.addListener('keyboardWillShow', () => document.documentElement.classList.add('keyboard-open'));
Keyboard.addListener('keyboardWillHide', () => document.documentElement.classList.remove('keyboard-open'));

Object.assign(window, {
  GepardNative: {
    apiBase: API_BASE,
    isNative: true,
    ready,
    getToken,
    setToken,
    clearToken,
    getReminder,
    setReminder,
    openExternal: (url: string) => Browser.open({ url }),
  },
});

declare global {
  interface Window {
    GepardNative?: {
      apiBase: string;
      isNative: boolean;
      ready: Promise<void>;
      getToken(): Promise<string | null>;
      setToken(token: string): Promise<void>;
      clearToken(): Promise<void>;
      getReminder(): Promise<Reminder>;
      setReminder(reminder: Reminder): Promise<boolean>;
      openExternal(url: string): Promise<void>;
    };
  }
}
