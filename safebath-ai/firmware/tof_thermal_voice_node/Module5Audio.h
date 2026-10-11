#ifndef SAFEBATH_MODULE5_AUDIO_H
#define SAFEBATH_MODULE5_AUDIO_H

#include <Arduino.h>
#include <HardwareSerial.h>
#include <DFRobotDFPlayerMini.h>

namespace safebath {

enum class AudioError : uint8_t {
  None,
  WaitingForCard,
  CardRemoved,
  InvalidPrompt,
  PlayerNotReady,
  PlaybackBusy,
  PlayerReportedError,
  PlaybackTimeout,
};

enum class PlaybackState : uint8_t {
  Idle,
  Playing,    // best effort: command sent; DFPlayer has no start ACK here
  Completed,  // DFPlayer reported DFPlayerPlayFinished
  Failed,
};

/*
 * Non-blocking DFPlayer wrapper for ESP32 UART2. begin() attaches the UART
 * without reset/ACK waits; poll() drains a bounded number of events and does
 * delayed UART reinitialization when the SD card/player is unavailable.
 */
class Module5Audio {
 public:
  Module5Audio(HardwareSerial& uart, DFRobotDFPlayerMini& player,
               int8_t rxPin, int8_t txPin)
      : uart_(uart), player_(player), rxPin_(rxPin), txPin_(txPin) {}

  void begin(uint8_t initialVolume = 22) {
    setVolume(initialVolume);
    uart_.begin(9600, SERIAL_8N1, rxPin_, txPin_);
    uartStarted_ = true;
    attachPlayer();
    lastError_ = AudioError::WaitingForCard;
    lastAttemptAt_ = millis();
    retryDelayMs_ = kInitialRetryMs;
    Serial.println("[dfp] UART2 attached; waiting for SD card status");
  }

  void poll() {
    if (!uartStarted_) return;

    // Bound event work so continuous UART noise cannot starve sensor polling.
    for (uint8_t count = 0; count < kMaxEventsPerPoll && player_.available(); ++count) {
      const uint8_t type = player_.readType();
      const uint16_t value = player_.read();
      handleEvent(type, value);
    }

    const uint32_t now = millis();
    if (state_ == PlaybackState::Playing &&
        static_cast<uint32_t>(now - playbackStartedAt_) >= kPlaybackTimeoutMs) {
      state_ = PlaybackState::Failed;
      lastError_ = AudioError::PlaybackTimeout;
      Serial.println("[dfp] playback completion timeout; status is unverified");
    }

    if (!cardOnline_ && static_cast<uint32_t>(now - lastAttemptAt_) >= retryDelayMs_) {
      retryInterface(now);
    }
  }

  bool playPrompt(uint16_t promptNumber) {
    if (promptNumber < 1 || promptNumber > 4) {
      lastError_ = AudioError::InvalidPrompt;
      return false;
    }
    if (!cardOnline_) {
      lastError_ = AudioError::PlayerNotReady;
      return false;
    }
    if (state_ == PlaybackState::Playing) {
      lastError_ = AudioError::PlaybackBusy;
      return false;
    }

    player_.playMp3Folder(promptNumber);
    activePrompt_ = promptNumber;
    playbackStartedAt_ = millis();
    state_ = PlaybackState::Playing;
    lastError_ = AudioError::None;
    Serial.printf("[dfp] play command sent for /mp3/%04u.mp3; awaiting completion event\n",
                  static_cast<unsigned>(promptNumber));
    return true;
  }

  uint8_t setVolume(uint8_t volume) {
    volume_ = volume > 30 ? 30 : volume;
    if (cardOnline_) player_.volume(volume_);
    return volume_;
  }

  bool stopPlayback() {
    if (!cardOnline_) {
      lastError_ = AudioError::PlayerNotReady;
      return false;
    }
    player_.stop();
    state_ = PlaybackState::Idle;
    activePrompt_ = 0;
    lastError_ = AudioError::None;
    Serial.println("[dfp] stop command sent; physical stop is not independently sensed");
    return true;
  }

  bool isReady() const { return cardOnline_; }
  bool isPlaying() const { return state_ == PlaybackState::Playing; }
  PlaybackState playbackState() const { return state_; }
  AudioError lastErrorCode() const { return lastError_; }
  uint16_t activePrompt() const { return activePrompt_; }

  const char* lastError() const {
    switch (lastError_) {
      case AudioError::None: return "none";
      case AudioError::WaitingForCard: return "waiting_for_sd_card";
      case AudioError::CardRemoved: return "sd_card_removed";
      case AudioError::InvalidPrompt: return "prompt_not_allowlisted";
      case AudioError::PlayerNotReady: return "player_not_ready";
      case AudioError::PlaybackBusy: return "playback_busy";
      case AudioError::PlayerReportedError: return "dfplayer_error_event";
      case AudioError::PlaybackTimeout: return "completion_event_timeout";
    }
    return "unknown";
  }

 private:
  static const uint8_t kMaxEventsPerPoll = 8;
  static const uint32_t kInitialRetryMs = 5000;
  static const uint32_t kMaxRetryMs = 30000;
  static const uint32_t kPlaybackTimeoutMs = 120000;

  void attachPlayer() {
    // ACKs and reset waits are disabled: DFRobot's begin(..., false, false)
    // attaches the stream without waiting for a response from a failed player.
    player_.begin(uart_, false, false);
    player_.reset();  // ACK is off, so the library's send delay is bounded (~10ms).
  }

  void retryInterface(uint32_t now) {
    lastAttemptAt_ = now;
    Serial.println("[dfp] no SD online event; restarting UART/player interface");
    uart_.end();
    uart_.begin(9600, SERIAL_8N1, rxPin_, txPin_);
    attachPlayer();
    lastError_ = AudioError::WaitingForCard;
    retryDelayMs_ = retryDelayMs_ >= kMaxRetryMs / 2
                        ? kMaxRetryMs : retryDelayMs_ * 2;
  }

  void handleEvent(uint8_t type, uint16_t value) {
    switch (type) {
      case DFPlayerCardOnline:
      case DFPlayerCardInserted:
        cardOnline_ = true;
        retryDelayMs_ = kInitialRetryMs;
        lastError_ = AudioError::None;
        player_.volume(volume_);
        Serial.printf("[dfp] SD card online; volume=%u\n", volume_);
        break;
      case DFPlayerCardRemoved:
        cardOnline_ = false;
        state_ = PlaybackState::Failed;
        lastError_ = AudioError::CardRemoved;
        Serial.println("[dfp] SD card removed");
        break;
      case DFPlayerPlayFinished:
        state_ = PlaybackState::Completed;
        activePrompt_ = 0;
        lastError_ = AudioError::None;
        Serial.printf("[dfp] completion event, track=%u\n", value);
        break;
      case DFPlayerError:
        state_ = PlaybackState::Failed;
        activePrompt_ = 0;
        lastError_ = AudioError::PlayerReportedError;
        Serial.printf("[dfp] player error event code=%u\n", value);
        break;
      default:
        break;
    }
  }

  HardwareSerial& uart_;
  DFRobotDFPlayerMini& player_;
  int8_t rxPin_;
  int8_t txPin_;
  bool uartStarted_ = false;
  bool cardOnline_ = false;
  uint8_t volume_ = 22;
  uint16_t activePrompt_ = 0;
  uint32_t lastAttemptAt_ = 0;
  uint32_t retryDelayMs_ = kInitialRetryMs;
  uint32_t playbackStartedAt_ = 0;
  PlaybackState state_ = PlaybackState::Idle;
  AudioError lastError_ = AudioError::WaitingForCard;
};

}  // namespace safebath

#endif  // SAFEBATH_MODULE5_AUDIO_H
