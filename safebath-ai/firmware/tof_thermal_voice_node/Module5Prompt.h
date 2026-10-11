#ifndef SAFEBATH_MODULE5_PROMPT_H
#define SAFEBATH_MODULE5_PROMPT_H

#include <stdint.h>
#include <string.h>

namespace safebath {

// These names match the existing voice-request prompt field. Track numbers
// map to SD:/mp3/000N.mp3 through DFRobotDFPlayerMini::playMp3Folder().
inline uint16_t module5PromptTrack(const char* name) {
  if (!name) return 0;
  if (strcmp(name, "are_you_ok") == 0) return 1;
  if (strcmp(name, "help") == 0) return 2;
  if (strcmp(name, "emergency_procedure") == 0) return 3;
  if (strcmp(name, "safety_check") == 0) return 4;
  return 0;
}

inline bool module5PromptAllowed(uint16_t track) {
  return track >= 1 && track <= 4;
}

}  // namespace safebath

#endif  // SAFEBATH_MODULE5_PROMPT_H
