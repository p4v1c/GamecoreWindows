/** One external disk — see api.storage. */
export interface StorageVolume {
  name: string
  /** `E:`. The handle for mount/unmount: a row number is not one, since
   *  a disk arriving while the screen is open renumbers the list. */
  device: string
  label: string
  uuid: string
  fstype: string
  size: string
  /** The drive root (`E:\`). Not stable across replugs: do not record it. */
  mountpoint: string
  mounted: boolean
  slug: string
  /** `<DATA>/volumes/<slug>` — what a romsPath should point at. Survives a
   *  replug, which the drive letter does not: Windows gives the next free
   *  letter, so the same disk can come back as `F:`. */
  stable_path: string
  /** false for FAT32/exFAT (no journal): ROMs are fine, saves are not. */
  keeps_permissions: boolean
  /** The sentence to show when keeps_permissions is false; "" otherwise. */
  saves_warning: string
}
