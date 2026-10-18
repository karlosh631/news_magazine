"use client";

import { useState } from "react";

/**
 * Article media that is not the lead image.
 *
 * Only renders media the ingestion worker actually found on the
 * outlet's page: an <audio> element for podcasts/voice notes, or a
 * <video> element for a direct file URL. Embeds are handled by the
 * article page itself, so nothing is rendered twice.
 */
export function ArticleMedia({
  audioUrl,
  videoUrl,
  hasEmbed,
}: {
  audioUrl: string | null;
  videoUrl: string | null;
  hasEmbed: boolean;
}) {
  const [audioFailed, setAudioFailed] = useState(false);
  const [videoFailed, setVideoFailed] = useState(false);

  const directVideo =
    !hasEmbed && videoUrl && /\.(mp4|webm|mov|ogg|ogv)(\?|$)/i.test(videoUrl)
      ? videoUrl
      : null;

  const showAudio = Boolean(audioUrl) && !audioFailed;
  const showVideo = Boolean(directVideo) && !videoFailed;

  if (!showAudio && !showVideo) return null;

  return (
    <div className="mt-6 space-y-4">
      {showVideo && directVideo && (
        <video
          src={directVideo}
          controls
          preload="metadata"
          playsInline
          className="w-full rounded-xl"
          onError={() => setVideoFailed(true)}
        />
      )}

      {showAudio && audioUrl && (
        <div className="rounded-lg border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-800">
          <span className="mb-2 block text-[10px] font-semibold uppercase tracking-wider text-gray-500">
            Audio
          </span>
          <audio
            src={audioUrl}
            controls
            preload="none"
            className="w-full"
            onError={() => setAudioFailed(true)}
          />
        </div>
      )}
    </div>
  );
}