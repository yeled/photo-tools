// merge-helper: apply a dedupe_photos.py merge to the Photos library via
// PhotoKit -- the supported change API, so everything syncs to iCloud exactly
// like edits made by hand in Photos.
//
// Reads a JSON manifest and processes whichever sections are present:
//
//   {
//     "dates":     [{"uuid": "ABC-...", "date": "2016-08-19T09:12:44"}, ...],
//     "favorites": ["ABC-...", ...],
//     "deletes":   ["DEF-...", ...]
//   }
//
// dates/favorites are plain metadata edits (no confirmation dialog, batched).
// deletes go through ONE PHAssetChangeRequest.deleteAssets call, so macOS
// shows exactly one confirmation dialog for the whole run and everything
// lands in Recently Deleted (30-day recovery).
//
// Usage:
//   merge-helper --manifest apply.json            dry run: validate + report
//   merge-helper --manifest apply.json --apply    actually write
//
// Dates are naive local time ("yyyy-MM-dd'T'HH:mm:ss"), matching the rest of
// the repo. Output is line-oriented: "DATE <uuid> ok|ERROR ...", a MISSING
// line per unresolvable uuid, and a final summary. Exit codes: 0 ok,
// 1 failures, 2 bad input, 3 user cancelled the delete dialog.

import Foundation
import Photos

// --- arguments ---------------------------------------------------------------
let args = CommandLine.arguments
if args.contains("--help") || args.contains("-h") {
    print("""
    usage: merge-helper --manifest PATH [--apply]
      --manifest PATH  JSON manifest with any of: dates, favorites, deletes
      --apply          write; without it, validate and report only
    """)
    exit(0)
}
let dryRun = !args.contains("--apply")
var manifestPath: String? = nil
if let i = args.firstIndex(of: "--manifest"), i + 1 < args.count { manifestPath = args[i + 1] }
guard let manifestPath else {
    FileHandle.standardError.write("merge-helper: --manifest PATH is required (--help for usage)\n".data(using: .utf8)!)
    exit(2)
}

struct DateEdit: Decodable {
    let uuid: String
    let date: String
}
struct Manifest: Decodable {
    var dates: [DateEdit]?
    var favorites: [String]?
    var deletes: [String]?
}

let manifest: Manifest
do {
    let data = try Data(contentsOf: URL(fileURLWithPath: manifestPath))
    manifest = try JSONDecoder().decode(Manifest.self, from: data)
} catch {
    FileHandle.standardError.write("merge-helper: cannot read manifest: \(error.localizedDescription)\n".data(using: .utf8)!)
    exit(2)
}

let dateFormatter = DateFormatter()
dateFormatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
dateFormatter.locale = Locale(identifier: "en_US_POSIX")
dateFormatter.timeZone = TimeZone.current

var parsedDates: [(uuid: String, date: Date)] = []
var badDates: [String] = []
for edit in manifest.dates ?? [] {
    if let d = dateFormatter.date(from: edit.date) {
        parsedDates.append((edit.uuid.uppercased(), d))
    } else {
        badDates.append("\(edit.uuid) \(edit.date)")
    }
}
if !badDates.isEmpty {
    for b in badDates { print("BADDATE \(b)") }
    exit(2)
}
let favorites = (manifest.favorites ?? []).map { $0.uppercased() }
let deletes = (manifest.deletes ?? []).map { $0.uppercased() }

if parsedDates.isEmpty && favorites.isEmpty && deletes.isEmpty {
    print("Manifest has no dates, favorites, or deletes; nothing to do.")
    exit(0)
}

// --- authorization -----------------------------------------------------------
var status = PHPhotoLibrary.authorizationStatus(for: .readWrite)
if status == .notDetermined {
    let sem = DispatchSemaphore(value: 0)
    PHPhotoLibrary.requestAuthorization(for: .readWrite) { s in
        status = s
        sem.signal()
    }
    sem.wait()
}
guard status == .authorized else {
    print("Photos access not granted (status \(status.rawValue)).")
    print("Grant full Photos access in System Settings > Privacy & Security > Photos, then rerun.")
    exit(1)
}

// --- fetch -------------------------------------------------------------------
func fetchAssets(_ uuids: [String]) -> ([String: PHAsset], [String]) {
    guard !uuids.isEmpty else { return ([:], []) }
    let opts = PHFetchOptions()
    opts.includeHiddenAssets = true
    var found: [String: PHAsset] = [:]
    PHAsset.fetchAssets(withLocalIdentifiers: uuids, options: opts)
        .enumerateObjects { a, _, _ in
            found[String(a.localIdentifier.prefix(36)).uppercased()] = a
        }
    let missing = uuids.map { $0.uppercased() }.filter { found[$0] == nil }
    return (found, missing)
}

let allUuids = Array(Set(parsedDates.map { $0.uuid } + favorites + deletes)).sorted()
let (assets, missing) = fetchAssets(allUuids)
for m in missing { print("MISSING \(m)") }

var failed = 0
let batchSize = 500

// --- dates -------------------------------------------------------------------
let dateWork = parsedDates.filter { assets[$0.uuid] != nil }
if !dateWork.isEmpty {
    print("\(dryRun ? "Would set" : "Setting") creationDate on \(dateWork.count) asset(s)...")
    if !dryRun {
        for chunk in stride(from: 0, to: dateWork.count, by: batchSize)
            .map({ Array(dateWork[$0..<min($0 + batchSize, dateWork.count)]) }) {
            do {
                try PHPhotoLibrary.shared().performChangesAndWait {
                    for item in chunk {
                        guard let asset = assets[item.uuid] else { continue }
                        let req = PHAssetChangeRequest(for: asset)
                        req.creationDate = item.date
                    }
                }
                for item in chunk { print("DATE \(item.uuid) ok") }
            } catch {
                failed += chunk.count
                for item in chunk { print("DATE \(item.uuid) ERROR \(error.localizedDescription)") }
            }
        }
    }
}

// --- favorites ---------------------------------------------------------------
let favWork = favorites.filter { assets[$0] != nil }
if !favWork.isEmpty {
    print("\(dryRun ? "Would favorite" : "Favoriting") \(favWork.count) asset(s)...")
    if !dryRun {
        do {
            try PHPhotoLibrary.shared().performChangesAndWait {
                for uuid in favWork {
                    guard let asset = assets[uuid] else { continue }
                    let req = PHAssetChangeRequest(for: asset)
                    req.isFavorite = true
                }
            }
            for uuid in favWork { print("FAV \(uuid) ok") }
        } catch {
            failed += favWork.count
            for uuid in favWork { print("FAV \(uuid) ERROR \(error.localizedDescription)") }
        }
    }
}

// --- deletes: ONE change request, ONE system dialog --------------------------
let delWork = deletes.filter { assets[$0] != nil }
if !delWork.isEmpty {
    print("\(dryRun ? "Would delete" : "Deleting") \(delWork.count) asset(s) "
          + "(to Recently Deleted; one confirmation dialog)...")
    if !dryRun {
        let toDelete = delWork.compactMap { assets[$0] }
        do {
            try PHPhotoLibrary.shared().performChangesAndWait {
                PHAssetChangeRequest.deleteAssets(toDelete as NSArray)
            }
            for uuid in delWork { print("DELETE \(uuid) ok") }
        } catch {
            let nsErr = error as NSError
            if nsErr.domain == PHPhotosErrorDomain,
               nsErr.code == PHPhotosError.userCancelled.rawValue {
                print("CANCELLED delete dialog dismissed; nothing deleted")
                exit(3)
            }
            failed += delWork.count
            for uuid in delWork { print("DELETE \(uuid) ERROR \(error.localizedDescription)") }
        }
    }
}

// --- summary -----------------------------------------------------------------
let verb = dryRun ? "validated" : "done"
print("\nmerge-helper \(verb): \(dateWork.count) date(s), \(favWork.count) favorite(s), "
      + "\(delWork.count) delete(s), \(missing.count) missing, \(failed) failed.")
if dryRun {
    print("Dry run only -- nothing written. Rerun with --apply to write.")
}
exit(failed > 0 ? 1 : 0)
