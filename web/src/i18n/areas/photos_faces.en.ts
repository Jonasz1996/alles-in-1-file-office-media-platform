import type { Translation } from "../types";
import type nl from "./photos_faces.nl";

const en: Translation<typeof nl> = {
  title: "Faces",
  notReady:
    "Face recognition is not running on this server: the analysis is off, or not fully installed. The end text of the install script says why, at Gezichten (de container analyse). If it says ANALYSE=uit, run the script again with ANALYSE=aan; then everyone turns it on for themselves under Account → Photos. See docs/runbooks/analyse.md.",
  switch: {
    label: "Recognise faces in my photos",
    consent:
      "The platform looks for faces in your own photos and suggests who they are. To do that it turns each face into a series of numbers. Such a series is biometric data, also of people in your photos who chose nothing themselves. The numbers are stored encrypted on this server, only with your photos. Other users never see them. Whoever manages the server can technically get to them. The platform's own backups do not contain them; snapshots the administrator makes do, encrypted. If you turn this off, we erase the faces, the numbers and the key. After that, those copies can no longer be read either. If you use the platform for people other than your family, first read the explanation in the README.",
    on: "Turn on",
    offTitle: "Turn faces off?",
    offText:
      "We erase all faces found and their numbers. People you named or confirmed yourself stay on your photos.",
    off: "Turn off and erase",
    turnedOn: "Face recognition is on.",
    turnedOff: "Face recognition is off; the faces are being erased.",
  },
  status: {
    scanning: "Looking for faces: {done} of {total} photos",
    done: "Done: {faces} faces in {photos} photos",
    waiting: "Waiting for the analysis",
    forbidden: "The administrator has turned off face recognition",
    wiping: "Faces are being erased …",
    failed: { one: "{n} photo failed", other: "{n} photos failed" },
    off: "Face recognition is off.",
    read: "Read what that means",
    where: "You turn it on under Account, at Photos.",
  },
  suggest: {
    title: "Is this …?",
    question: "Is this {name}?",
    count: { one: "{n} photo", other: "{n} photos" },
    yes: "Yes",
    no: "No",
    hide: "Hide",
    maybe: "Maybe also {name}",
  },
  group: {
    title: "Who is this?",
    count: { one: "{n} face", other: "{n} faces" },
    name: "Name",
    selected: "{n} of {total} selected",
    confirm: "Confirm",
    pick: "Choose a name first.",
  },
  grid: {
    open: "View",
    close: "Close",
    more: "Load more",
    face: "Face {i} of {total}",
    help: "Arrows: to a face. Enter: open the photo. Esc: close. Ctrl+Z: undo.",
    helpSuggest: "Y: yes, N: no, H: hide.",
    helpGroup: "Space: select or not. H: hide.",
    empty: "Nothing left here.",
    chosenYes: "yes",
    chosenNo: "no",
    chosenHidden: "hidden",
  },
  done: {
    yes: { one: "{n} face confirmed as {name}.", other: "{n} faces confirmed as {name}." },
    no: "Not {name}.",
    hidden: "Will no longer be shown.",
    undo: "Undo",
    undone: "Undone.",
  },
  wipe: {
    button: "Erase unnamed faces",
    text: {
      one: "We erase {n} face you did not name, with its numbers. It does not come back, not even after a new search round.",
      other:
        "We erase {n} faces you did not name, with their numbers. They do not come back, not even after a new search round.",
    },
    confirm: "Erase",
    queued: { one: "{n} face is being erased.", other: "{n} faces are being erased." },
  },
  row: {
    title: "Faces",
    none: "No faces found in this photo.",
    unknown: "?",
    who: "Who is this?",
    rescan: "Search again",
    rescanned: "This photo is now at the front of the queue.",
    cancel: "Cancel",
  },
};

export default en;
