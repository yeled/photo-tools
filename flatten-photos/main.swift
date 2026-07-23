// flatten-photos: collapse single-album "wrapper" folders in Apple Photos.
// Structure: .../LR/YYYY/YYYY-mm-dd/<album>  ->  album moves up into YYYY,
// and the emptied YYYY-mm-dd wrapper folder is deleted.
//
// Unlike AppleScript, PhotoKit does a TRUE move: the album keeps its
// identity, sort order, and key photo.
//
// Usage:
//   flatten-photos                       dry run, whole library
//   flatten-photos --scope LR            dry run, only wrappers whose path contains "LR"
//   flatten-photos --scope LR --apply    actually do it
//   --keep-empty                         leave the emptied wrapper folders in place

import Foundation
import Photos

let args = CommandLine.arguments
let dryRun = !args.contains("--apply")
let keepEmpty = args.contains("--keep-empty")
var scope: String? = nil
if let i = args.firstIndex(of: "--scope"), i + 1 < args.count { scope = args[i + 1] }

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

// --- discover wrapper folders ------------------------------------------------
struct Job {
    let album: PHAssetCollection
    let albumTitle: String
    let wrapper: PHCollectionList
    let wrapperName: String
    let dest: PHCollectionList
    let destPath: String
}

var jobs: [Job] = []

func children(of list: PHCollectionList) -> (albums: [PHAssetCollection], folders: [PHCollectionList]) {
    var albums: [PHAssetCollection] = []
    var folders: [PHCollectionList] = []
    PHCollection.fetchCollections(in: list, options: nil).enumerateObjects { c, _, _ in
        if let a = c as? PHAssetCollection { albums.append(a) }
        if let l = c as? PHCollectionList { folders.append(l) }
    }
    return (albums, folders)
}

// Walk downward; a child folder with exactly one album and no subfolders is a
// wrapper, and its parent (the folder we're standing in) is the destination.
func walk(_ list: PHCollectionList, path: String) {
    for child in children(of: list).folders {
        let name = child.localizedTitle ?? "?"
        let (albums, folders) = children(of: child)
        if albums.count == 1 && folders.isEmpty {
            jobs.append(Job(album: albums[0],
                            albumTitle: albums[0].localizedTitle ?? "?",
                            wrapper: child,
                            wrapperName: name,
                            dest: list,
                            destPath: path))
        } else {
            walk(child, path: path + " > " + name)
        }
    }
}

PHCollectionList.fetchTopLevelUserCollections(with: nil).enumerateObjects { c, _, _ in
    if let root = c as? PHCollectionList {
        walk(root, path: root.localizedTitle ?? "?")
    }
}

if let s = scope {
    jobs = jobs.filter { ($0.destPath + " > " + $0.wrapperName).localizedCaseInsensitiveContains(s) }
}

if jobs.isEmpty {
    print("No single-album wrapper folders found\(scope.map { " matching scope \"\($0)\"" } ?? "").")
    exit(0)
}

// --- execute -----------------------------------------------------------------
var moved = 0, failed = 0
for job in jobs {
    let label = "\(job.albumTitle)  (\(job.wrapperName) -> \(job.destPath))"
    if dryRun {
        print("MOVE  " + label)
        continue
    }
    do {
        try PHPhotoLibrary.shared().performChangesAndWait {
            guard let rm = PHCollectionListChangeRequest(for: job.wrapper),
                  let add = PHCollectionListChangeRequest(for: job.dest) else { return }
            rm.removeChildCollections([job.album] as NSArray)
            add.addChildCollections([job.album] as NSArray)
        }
        var note = ""
        if !keepEmpty {
            // deleting a folder deletes its contents, so re-verify it is empty first
            let remaining = PHCollection.fetchCollections(in: job.wrapper, options: nil)
            if remaining.count == 0 {
                try PHPhotoLibrary.shared().performChangesAndWait {
                    PHCollectionListChangeRequest.deleteCollectionLists([job.wrapper] as NSArray)
                }
                note = "  (removed empty \(job.wrapperName))"
            } else {
                note = "  (wrapper not empty after move — kept)"
            }
        }
        print("MOVED " + label + note)
        moved += 1
    } catch {
        print("ERROR " + label + ": \(error.localizedDescription)")
        failed += 1
    }
}

if dryRun {
    print("\n\(jobs.count) album(s) would move. Rerun with --apply to do it.")
} else {
    print("\nDone: \(moved) moved, \(failed) failed.")
}
