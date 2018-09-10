<?php
/**
 * UpdateOnSave extension hooks
 *
 * @file
 * @ingroup Extensions
 * @license BSD-2-Clause
 */
use MediaWiki\Logger\LoggerFactory;



class UpdateOnSaveHooks {

      public static function onPageContentSaveComplete( &$wikiPage, &$user, $content, $summary, $isMinor, $isWatch, $section, &$flags, $revision, &$status, $baseRevId, $undidRevId ) {
          if ($isMinor) {
              return true;
          }
          $wgUpdateOnSaveEndpoint = "http://localhost/devel/build?repo=mediawiki&action=update&stream=true&basefile=";
	  $logger = LoggerFactory::getInstance( 'UpdateOnSave' );
          $logger->info("hook is called");
          $ch = curl_init();
	  $url = $wgUpdateOnSaveEndpoint . $wikiPage->getTitle();
          $logger->info("hook url is " . $url);
	  curl_setopt($ch, CURLOPT_URL, $url);
	  curl_setopt($ch, CURLOPT_RETURNTRANSFER, 1);
	  $output = curl_exec($ch);
	  $info = curl_getinfo($ch);
          if ($output === false) {
              $logger->error("curl error: " . curl_error($ch));
	      $logger->error("status code: " . $info["http_code"]);
	      $logger->error("size_download: " . $info["size_download"]);
          } else {
	      $logger->info("hook curl exec returns " . $output);
          }
	  curl_close($ch);
	  return true;      
      }
}
