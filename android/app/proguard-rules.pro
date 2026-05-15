# kotlinx.serialization keeps the generated serializers reachable
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.AnnotationsKt

-keep,includedescriptorclasses class dev.orivael.axiom.**$$serializer { *; }
-keepclassmembers class dev.orivael.axiom.** {
    *** Companion;
}
-keepclasseswithmembers class dev.orivael.axiom.** {
    kotlinx.serialization.KSerializer serializer(...);
}
