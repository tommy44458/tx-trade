-- Downloads started from the site, one row per day and kind of click. No IPs or user agents.
CREATE TABLE site_downloads (
  day TEXT NOT NULL,
  platform TEXT NOT NULL,
  source TEXT NOT NULL,
  locale TEXT NOT NULL,
  country TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, platform, source, locale, country)
);

-- GitHub's lifetime download count of each release asset, recorded once a day.
-- Its daily change is that day's downloads, which for update files include in-app updates.
CREATE TABLE github_downloads (
  day TEXT NOT NULL,
  version TEXT NOT NULL,
  asset TEXT NOT NULL,
  total INTEGER NOT NULL,
  PRIMARY KEY (day, asset)
);
