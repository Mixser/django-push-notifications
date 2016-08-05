from __future__ import unicode_literals

import json
import collections

from django.db import models
from django.utils.encoding import python_2_unicode_compatible
from django.utils.translation import ugettext_lazy as _

from .settings import PUSH_NOTIFICATIONS_SETTINGS as SETTINGS


def get_device_model_by_type(device_type):
    if device_type == Device.APNS:
        return APNSDevice
    elif device_type == Device.GCM:
        return GCMDevice

    return None


class Notification(models.Model):
    devices = models.ManyToManyField('Device', related_name='sent_notifications')
    message = models.TextField()
    kwargs = models.TextField()
    sent_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def create_notification_for(cls, devices, message, **kwargs):
        notification = cls(message=message, kwargs=json.dumps(kwargs))
        notification.save()

        if not isinstance(devices, collections.Iterable):
            devices = [devices, ]

        notification.devices.add(*devices)

    def __unicode__(self):
        if self.devices.count() > 1:
            others_text = ' and %s other' % (self.devices.count() - 1)
        else:
            others_text = ''
        return u'%s%s: %s' % (self.devices.first(), others_text, self.message)


class DeviceQuerySet(models.query.QuerySet):
    def send_message(self, message, **kwargs):
        if self:
            devices = {}

            for id, device_type in self.values_list('pk', 'device_type'):
                devices[device_type] = devices.get(device_type, []) + [id]

            for key in devices.keys():
                device_model = get_device_model_by_type(key)
                device_model.objects.filter(id__in=devices[key]).send_message(message, **kwargs)


@python_2_unicode_compatible
class Device(models.Model):
    APNS = 0
    GCM = 1

    DEVICE_TYPES = (
        (APNS, 'APNS'),
        (GCM, 'GCM')
    )

    objects = DeviceQuerySet.as_manager()

    name = models.CharField(max_length=255, verbose_name=_("Name"), blank=True, null=True)
    active = models.BooleanField(verbose_name=_("Is active"), default=True,
                                 help_text=_("Inactive devices will not be sent notifications"))
    user = models.ForeignKey(SETTINGS["USER_MODEL"], blank=True, null=True)
    date_created = models.DateTimeField(verbose_name=_("Creation date"), auto_now_add=True, null=True)

    device_id = models.CharField(verbose_name=_("Device ID"), blank=False, null=True, unique=True, max_length=255)
    registration_id = models.TextField(verbose_name=_("Registration ID"))

    device_type = models.IntegerField(choices=DEVICE_TYPES, editable=False)

    def send_message(self, message, **kwargs):
        if self.__class__ != Device:
            return Notification.create_notification_for(self, message, **kwargs)

        if self.device_type == Device.APNS:
            self.__class__ = APNSDevice
        else:
            self.__class__ = GCMDevice

        self.send_message(message, **kwargs)

    def __str__(self):
        return self.name or \
               str(self.device_id or "") or \
               "%s for %s" % (self.__class__.__name__, self.user or "unknown user")


class GCMDeviceManager(models.Manager):
    def get_queryset(self):
        return GCMDeviceQuerySet(self.model).filter(device_type=Device.GCM)


class GCMDeviceQuerySet(models.query.QuerySet):
    def send_message(self, message, **kwargs):
        if self:
            from .gcm import gcm_send_bulk_message

            Notification.create_notification_for(self, message, **kwargs)

            data = kwargs.pop("extra", {})
            if message is not None:
                data["message"] = message

            reg_ids = list(self.filter(active=True).values_list('registration_id', flat=True))
            return gcm_send_bulk_message(registration_ids=reg_ids, data=data, **kwargs)


class GCMDevice(Device):
    # device_id cannot be a reliable primary key as fragmentation between different devices
    # can make it turn out to be null and such:
    # http://android-developers.blogspot.co.uk/2011/03/identifying-app-installations.html
    objects = GCMDeviceManager()

    class Meta:
        verbose_name = _("GCM device")
        proxy = True
        
    def save(self, *args, **kwargs):
        self.device_type = Device.GCM
        super(GCMDevice, self).save(*args, **kwargs)

    def send_message(self, message, **kwargs):
        from .gcm import gcm_send_message

        super(GCMDevice, self).send_message(message, **kwargs)

        data = kwargs.pop("extra", {})
        if message is not None:
            data["message"] = message
        return gcm_send_message(registration_id=self.registration_id, data=data, **kwargs)


class APNSDeviceManager(models.Manager):
    def get_queryset(self):
        return APNSDeviceQuerySet(self.model).filter(device_type=Device.APNS)


class APNSDeviceQuerySet(models.query.QuerySet):
    def send_message(self, message, **kwargs):
        if self:
            from .apns import apns_send_bulk_message

            Notification.create_notification_for(self, message, **kwargs)

            reg_ids = list(self.filter(active=True).values_list('registration_id', flat=True))
            return apns_send_bulk_message(registration_ids=reg_ids, alert=message, **kwargs)


class APNSDevice(Device):
    objects = APNSDeviceManager()

    class Meta:
        verbose_name = _("APNS device")
        proxy = True

    def save(self, *args, **kwargs):
        self.device_type = Device.APNS
        super(APNSDevice, self).save(*args, **kwargs)

    def send_message(self, message, **kwargs):
        from .apns import apns_send_message
        super(APNSDevice, self).send_message(message, **kwargs)
        return apns_send_message(registration_id=self.registration_id, alert=message, **kwargs)


# This is an APNS-only function right now, but maybe GCM will implement it
# in the future.  But the definition of 'expired' may not be the same. Whatevs
def get_expired_tokens(cerfile=None):
    from .apns import apns_fetch_inactive_ids
    return apns_fetch_inactive_ids(cerfile)
